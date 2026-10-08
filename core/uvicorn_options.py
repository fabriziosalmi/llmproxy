"""What the ``server:`` config block means to the listener.

``server.tls.*`` and ``server.keep_alive`` were documented, validated for the
"TLS is disabled" warning, and read by nothing: the listener was built from host
and port alone. Setting ``tls.enabled: true`` silenced the warning and served
plain HTTP, which is worse than having no setting. This turns the block into
uvicorn options and refuses a TLS configuration it cannot honour.
"""

from __future__ import annotations

import os
import ssl
from typing import Any

#: Seconds uvicorn waits for open connections (SSE streams, in-flight requests)
#: to finish after a stop signal, before closing them. Without a bound, a
#: supervisor's grace period decides, and ends in SIGKILL mid-stream.
DEFAULT_SHUTDOWN_TIMEOUT_S = 30

_TLS_VERSIONS = {
    "1.2": ssl.TLSVersion.TLSv1_2,
    "1.3": ssl.TLSVersion.TLSv1_3,
}


class TLSConfigError(ValueError):
    """server.tls is enabled but cannot be honoured."""


def _seconds(value: Any, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        return int(str(value).strip().rstrip("s"))
    except ValueError:
        raise ValueError(f"expected a number of seconds like 30 or 30s, got {value!r}") from None


def tls_min_version(server_cfg: dict[str, Any]) -> ssl.TLSVersion | None:
    tls = server_cfg.get("tls") or {}
    if not tls.get("enabled"):
        return None
    wanted = str(tls.get("min_version", "1.2"))
    try:
        return _TLS_VERSIONS[wanted]
    except KeyError:
        raise TLSConfigError(
            f"server.tls.min_version must be one of {sorted(_TLS_VERSIONS)}, got {wanted!r}"
        ) from None


def uvicorn_kwargs(config: dict[str, Any], *, check_files: bool = True) -> dict[str, Any]:
    """Keyword arguments for ``uvicorn.Config`` from the ``server`` block.

    ``check_files`` makes a missing certificate or key an error here, at start. The
    config validator passes False: it also runs on configuration submitted through
    the API, where probing for the existence of a caller-named path would make the
    endpoint a file-existence oracle. There the paths only have to be present.
    """
    server_cfg = config.get("server") or {}
    kwargs: dict[str, Any] = {
        "timeout_keep_alive": _seconds(server_cfg.get("keep_alive"), 5),
        "timeout_graceful_shutdown": _seconds(
            server_cfg.get("shutdown_timeout"), DEFAULT_SHUTDOWN_TIMEOUT_S
        ),
    }

    tls = server_cfg.get("tls") or {}
    if tls.get("enabled"):
        cert, key = tls.get("cert_file", ""), tls.get("key_file", "")
        for label, path in (("cert_file", cert), ("key_file", key)):
            if not isinstance(path, str) or not path:
                raise TLSConfigError(
                    f"server.tls.enabled is true but server.tls.{label} is not set; "
                    "refusing to start without the encryption that was asked for."
                )
            if check_files and not os.path.isfile(path):
                raise TLSConfigError(
                    f"server.tls.enabled is true but server.tls.{label} "
                    "does not name a readable file; refusing to start without the "
                    "encryption that was asked for."
                )
        tls_min_version(server_cfg)  # validates min_version
        kwargs["ssl_certfile"] = cert
        kwargs["ssl_keyfile"] = key
    return kwargs


def enforce_min_tls(uvicorn_config: Any, server_cfg: dict[str, Any]) -> None:
    """Raise the TLS floor on the context uvicorn built (it has no option for it)."""
    minimum = tls_min_version(server_cfg)
    if minimum is None:
        return
    uvicorn_config.load()  # builds .ssl; idempotent, serve() would do the same
    if uvicorn_config.ssl is not None:
        uvicorn_config.ssl.minimum_version = minimum
