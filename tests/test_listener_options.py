"""``server.tls`` and ``server.keep_alive`` do what the config says.

They were documented, used to suppress the "TLS is disabled" warning, and read by
nothing: ``tls.enabled: true`` served plain HTTP. The listener now gets them, a
TLS configuration that cannot be honoured stops the start, and the minimum TLS
version is enforced.
"""

import asyncio
import shutil
import ssl
import subprocess

import pytest
import uvicorn
from fastapi import FastAPI

from core.startup_checks import StartupError, validate_config
from core.uvicorn_options import (
    DEFAULT_SHUTDOWN_TIMEOUT_S,
    TLSConfigError,
    enforce_min_tls,
    uvicorn_kwargs,
)

needs_openssl = pytest.mark.skipif(shutil.which("openssl") is None, reason="no openssl")


@pytest.fixture
def cert(tmp_path):
    cert_file, key_file = tmp_path / "c.pem", tmp_path / "k.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key_file),
         "-out", str(cert_file), "-days", "1", "-subj", "/CN=localhost"],
        check=True, capture_output=True,
    )
    return str(cert_file), str(key_file)


def test_keep_alive_and_shutdown_reach_the_listener():
    kw = uvicorn_kwargs({"server": {"keep_alive": "60s", "shutdown_timeout": 12}})

    assert kw["timeout_keep_alive"] == 60
    assert kw["timeout_graceful_shutdown"] == 12
    assert uvicorn_kwargs({})["timeout_graceful_shutdown"] == DEFAULT_SHUTDOWN_TIMEOUT_S
    assert "ssl_certfile" not in kw


def test_tls_disabled_adds_no_ssl_options():
    assert "ssl_certfile" not in uvicorn_kwargs({"server": {"tls": {"enabled": False}}})


def test_enabled_tls_without_paths_refuses_to_start():
    for tls in ({"enabled": True}, {"enabled": True, "cert_file": "x"}):
        with pytest.raises(TLSConfigError, match="refusing to start"):
            uvicorn_kwargs({"server": {"tls": tls}})


def test_enabled_tls_with_unloadable_files_refuses_to_start(tmp_path):
    """Found out when the listener starts, not by probing the path in the validator."""
    cfg = {"server": {"tls": {"enabled": True, "cert_file": "/nope/c.pem", "key_file": "/nope/k.pem"}}}
    config = uvicorn.Config(FastAPI(), **uvicorn_kwargs(cfg))

    with pytest.raises(TLSConfigError, match="refusing to start"):
        enforce_min_tls(config, cfg["server"])


def test_the_startup_validator_reports_unset_paths_as_a_startup_error(monkeypatch):
    monkeypatch.setenv("LLM_PROXY_API_KEYS", "sk-proxy-test")
    cfg = {"server": {"port": 8090, "tls": {"enabled": True}}}

    with pytest.raises(StartupError, match="server.tls"):
        validate_config(cfg)


def test_the_validator_does_not_probe_the_filesystem_for_named_paths(monkeypatch):
    """It also runs on configuration submitted through the API; checking whether a
    caller-named path exists would turn that endpoint into a file-existence oracle.
    The listener checks the files when it starts."""
    monkeypatch.setenv("LLM_PROXY_API_KEYS", "sk-proxy-test")

    import os

    real = os.path.isfile

    def forbidden(path):
        if path in ("/x", "/y"):
            raise AssertionError(f"validate_config touched the filesystem: {path!r}")
        return real(path)

    monkeypatch.setattr(os.path, "isfile", forbidden)
    monkeypatch.setattr(os.path, "exists", lambda p: forbidden(p) if p in ("/x", "/y") else True)
    cfg = {"server": {"port": 8090, "tls": {"enabled": True, "cert_file": "/x", "key_file": "/y"}}}

    assert isinstance(validate_config(cfg), list)  # no error, no probe


@needs_openssl
def test_an_unknown_min_version_is_refused(cert):
    cfg = {"server": {"tls": {"enabled": True, "cert_file": cert[0], "key_file": cert[1],
                              "min_version": "1.1"}}}
    with pytest.raises(TLSConfigError, match="min_version"):
        uvicorn_kwargs(cfg)


@needs_openssl
async def test_a_tls_listener_serves_https_and_enforces_the_minimum(cert):
    cfg = {"server": {"tls": {"enabled": True, "cert_file": cert[0], "key_file": cert[1],
                              "min_version": "1.3"}}}
    config = uvicorn.Config(
        FastAPI(), host="127.0.0.1", port=0, log_level="error", **uvicorn_kwargs(cfg)
    )
    enforce_min_tls(config, cfg["server"])
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    try:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.05)
        port = server.servers[0].sockets[0].getsockname()[1]

        ok = ssl.create_default_context()
        ok.check_hostname, ok.verify_mode = False, ssl.CERT_NONE
        reader, writer = await asyncio.open_connection("127.0.0.1", port, ssl=ok)
        assert writer.get_extra_info("ssl_object").version() == "TLSv1.3"
        writer.close()

        old = ssl.create_default_context()
        old.check_hostname, old.verify_mode = False, ssl.CERT_NONE
        old.maximum_version = ssl.TLSVersion.TLSv1_2
        with pytest.raises((ssl.SSLError, ConnectionResetError)):
            await asyncio.open_connection("127.0.0.1", port, ssl=old)
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 10)
