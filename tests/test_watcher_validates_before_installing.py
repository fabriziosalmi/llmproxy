"""The reload path had none of the care the apply path has.

config_watch_loop hashed config.yaml every 30 seconds and, on a change,
assigned the parsed file straight onto the agent, then rebuilt the security
shield, the webhook dispatcher, the circuit-breaker thresholds, the cache
settings and the plugin set from it. Nothing called validate_config.

Meanwhile POST /api/v1/config/apply — identical content — parses, validates,
takes a timestamped atomic backup, hot-reloads and rolls back on failure. Two
completely different levels of care for the same operation, and the unguarded
one is the path an operator editing a file actually takes.

So a config whose api_keys_env named an unset variable left auth enabled with
zero valid keys and every request 401ing, where the same content at startup
would have refused to boot with a three-step fix. A port outside 1-65535 was
accepted because nothing rechecked it. A malformed fallback_chains entry was
installed and failed later as a KeyError inside the forwarder.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.startup_checks import StartupError, validate_config


class _Agent:
    """The surface config_watch_loop touches."""

    def __init__(self, good, candidate):
        self.config = good
        self._candidate = candidate
        self._config_hash = "old"
        self.webhooks = MagicMock()
        self.webhooks.close = AsyncMock()
        self.security = MagicMock()
        self.plugin_manager = MagicMock()
        self.circuit_manager = MagicMock()
        self.circuit_manager._circuits = {}

    def _compute_config_hash_sync(self):
        return "new"

    def _load_config(self):
        return self._candidate


def _valid_config(**over):
    cfg = {
        "server": {
            "port": 8090,
            "auth": {"enabled": False},
        },
        "endpoints": {},
        "security": {"max_payload_size_kb": 512},
    }
    cfg.update(over)
    return cfg


async def _one_iteration(agent):
    """Drive config_watch_loop for a single pass."""
    from proxy.background import config_watch_loop

    task = asyncio.create_task(config_watch_loop(agent, interval=0))
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


# ── a bad config is refused, and the old one keeps running ──────────────────


@pytest.mark.asyncio
async def test_an_invalid_port_is_not_installed():
    good = _valid_config()
    bad = _valid_config()
    bad["server"]["port"] = 99999
    agent = _Agent(good, bad)

    await _one_iteration(agent)

    assert agent.config is good, "the invalid config was installed anyway"


@pytest.mark.asyncio
async def test_auth_without_keys_is_not_installed(monkeypatch):
    """The case that produces a proxy 401ing every request."""
    monkeypatch.delenv("LLM_PROXY_NOT_SET_ANYWHERE", raising=False)
    good = _valid_config()
    bad = _valid_config()
    bad["server"]["auth"] = {
        "enabled": True,
        "api_keys_env": "LLM_PROXY_NOT_SET_ANYWHERE",
    }
    agent = _Agent(good, bad)

    await _one_iteration(agent)

    assert agent.config is good


@pytest.mark.asyncio
async def test_a_rejected_reload_is_logged_at_error(caplog):
    good = _valid_config()
    bad = _valid_config()
    bad["server"]["port"] = -1
    agent = _Agent(good, bad)

    with caplog.at_level("ERROR"):
        await _one_iteration(agent)

    assert any("REJECTED" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_a_rejected_hash_is_recorded_so_it_does_not_re_log():
    """A broken file must not produce an error every interval forever — the
    next EDIT has a different hash and is re-examined."""
    good = _valid_config()
    bad = _valid_config()
    bad["server"]["port"] = -1
    agent = _Agent(good, bad)

    await _one_iteration(agent)

    assert agent._config_hash == "new"


@pytest.mark.asyncio
async def test_a_valid_config_is_still_installed():
    """Validation must not break the feature it is guarding."""
    good = _valid_config()
    better = _valid_config()
    better["security"]["max_payload_size_kb"] = 256
    agent = _Agent(good, better)

    await _one_iteration(agent)

    assert agent.config is better


# ── the validator itself no longer fails cryptically ────────────────────────


def test_a_quoted_number_is_a_startup_error_not_a_typeerror():
    """`max_payload_size_kb: "512"` is a natural thing to write and parses as a
    string. The comparison raised TypeError, which run_startup_checks did not
    catch, so the process died with a traceback pointing at the validator
    rather than at the operator's config."""
    cfg = _valid_config()
    cfg["security"]["max_payload_size_kb"] = "512"

    with pytest.raises(StartupError, match="must be a number"):
        validate_config(cfg)


def test_the_error_says_how_to_fix_it():
    cfg = _valid_config()
    cfg["security"]["max_payload_size_kb"] = "512"

    with pytest.raises(StartupError) as exc:
        validate_config(cfg)

    assert "quotes" in str(exc.value)


def test_a_real_number_still_passes():
    cfg = _valid_config()
    cfg["security"]["max_payload_size_kb"] = 256

    validate_config(cfg)  # must not raise


def test_an_unexpected_exception_becomes_a_named_failure(monkeypatch, caplog):
    """The validator must not be the one component that fails cryptically."""
    import core.startup_checks as startup_checks

    def _boom(config):
        raise RuntimeError("something the validator did not anticipate")

    monkeypatch.setattr(startup_checks, "validate_config", _boom)

    with caplog.at_level("CRITICAL"):
        with pytest.raises(SystemExit):
            startup_checks.run_startup_checks({})

    assert any("Could not validate" in r.getMessage() for r in caplog.records)


# ── the chart no longer publishes a port nothing binds ──────────────────────


def test_the_chart_does_not_expose_the_unbound_admin_port():
    """server.admin.port is read by nothing — main.py binds one listener plus,
    optionally, the exporter — so the Service published an endpoint that
    refused every connection, and a port named "admin" implies a separate
    administrative surface with its own exposure decision."""
    import os

    import yaml

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    with open(os.path.join(root, "charts/llmproxy/values.yaml")) as f:
        values = yaml.safe_load(f)
    assert "adminPort" not in values["service"], "the value is still declared"

    chart_cfg = yaml.safe_load(values["config"])
    assert "admin" not in chart_cfg["server"], "the config still declares the port"

    # The templates must not reference it either — checked as usage rather
    # than as a substring, so a comment explaining its removal does not trip.
    for rel in (
        "charts/llmproxy/templates/service.yaml",
        "charts/llmproxy/templates/deployment.yaml",
    ):
        with open(os.path.join(root, rel)) as f:
            assert ".Values.service.adminPort" not in f.read(), f"{rel} uses it"


def test_the_shipped_config_does_not_declare_a_port_nothing_binds():
    """One layer up from the chart: config.yaml itself advertised the setting,
    and docs/reference/config.md documented it as "Admin port". A knob that
    nothing reads is worse than a missing one — an operator who changes it and
    finds the port closed has no way to tell a bug from a misunderstanding."""
    import os

    import yaml

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "config.yaml")) as f:
        cfg = yaml.safe_load(f)

    assert "admin" not in cfg["server"], "config.yaml still declares it"


def test_nothing_reads_server_admin():
    """The assertion the removal rests on. If a reader is ever added, this
    fails and the setting has to come back rather than be silently ignored."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parent.parent
    # A subscript read — server_cfg["admin"] or .get("admin") — with something
    # immediately before the bracket, so the RBAC role list ["admin"] and the
    # tuple element ("api_key", ["admin"]) are not mistaken for reads.
    pattern = re.compile(r"""(\.get\(["']admin["']|[\w\)\]]\[["']admin["']\])""")
    offenders = []
    for path in list((root / "core").rglob("*.py")) + list(
        (root / "proxy").rglob("*.py")
    ) + [root / "main.py"]:
        for n, line in enumerate(path.read_text().splitlines(), 1):
            # server.admin, not the "admin" RBAC role or an /admin route.
            if pattern.search(line):
                offenders.append(f"{path.relative_to(root)}:{n}: {line.strip()}")
    assert not offenders, "server.admin has a reader now:\n" + "\n".join(offenders)


# ── the WASM pool is released deliberately ──────────────────────────────────


def test_the_wasm_executor_is_shut_down_on_exit():
    """Eight worker threads were reclaimed by process exit rather than
    deliberately, and non-daemon pool threads can delay teardown."""
    import inspect

    import core.wasm_runner as wasm_runner
    import proxy.app_factory as app_factory

    assert callable(wasm_runner.shutdown_executor)
    assert "shutdown_executor" in inspect.getsource(app_factory)
