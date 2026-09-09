"""The configuration that ships must not answer without a credential.

`config.yaml` is baked into the published image by `COPY . .`, so whatever it
says is what every `docker run` gets. It said `server.auth.enabled: false`, and
the consequence was not theoretical: the README's own 30-second quickstart
produced a proxy answering /api/v1/registry, /api/v1/config/raw and /v1/models
with no credential at all. Passing LLM_PROXY_API_KEYS changed nothing, because
the middleware short-circuits on auth_enabled() before it reaches the key check
— so the whole inference/admin key tier was inert in the default deployment.

core/auth_policy.py already treats a MISSING key as True. An explicit `false`
in the shipped file was the one value that could defeat that, which is why the
file itself is asserted here rather than only the loader.
"""

import os

import pytest
import yaml

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(path: str) -> dict:
    with open(os.path.join(_REPO_ROOT, path)) as f:
        return yaml.safe_load(f) or {}


def test_the_shipped_config_authenticates():
    """The finding, stated directly against the file that ships."""
    cfg = _load("config.yaml")
    enabled = cfg["server"]["auth"]["enabled"]
    assert enabled is True, (
        "config.yaml is copied into the image, so this value is the default for "
        f"every docker run — got {enabled!r}"
    )


def test_the_shipped_config_names_the_admin_bag():
    """Segregating the control plane must be one env var away, not a code read."""
    auth = _load("config.yaml")["server"]["auth"]
    assert auth.get("admin_keys_env") == "LLM_PROXY_ADMIN_KEYS"


def test_the_chart_config_authenticates():
    """The chart embeds its own copy; it must not reopen what the image closed."""
    values = _load("charts/llmproxy/values.yaml")
    chart_cfg = yaml.safe_load(values["config"]) or {}
    enabled = chart_cfg["server"]["auth"]["enabled"]
    assert enabled is True, (
        "charts/llmproxy/values.yaml renders the pod's config via ConfigMap; "
        f"got {enabled!r}"
    )


def test_the_chart_config_names_the_admin_bag():
    chart_cfg = yaml.safe_load(_load("charts/llmproxy/values.yaml")["config"]) or {}
    assert chart_cfg["server"]["auth"].get("admin_keys_env") == "LLM_PROXY_ADMIN_KEYS"


# ── the documented way to run open ──────────────────────────────────────────


def test_dev_mode_turns_auth_off_even_when_a_config_file_exists(monkeypatch):
    """The override used to apply only when config.yaml was ABSENT.

    That made it useless in the case it exists for: the image always has a
    config file, so the only way to run open was to edit the shipped YAML —
    which is precisely what we just stopped doing.
    """
    from core.auth_policy import auth_enabled
    from proxy.config_loader import apply_dev_mode

    monkeypatch.setenv("LLM_PROXY_DEV_MODE", "1")
    cfg = {"server": {"auth": {"enabled": True}}}
    apply_dev_mode(cfg)

    assert auth_enabled(cfg) is False


def test_dev_mode_off_leaves_the_config_alone(monkeypatch):
    from core.auth_policy import auth_enabled
    from proxy.config_loader import apply_dev_mode

    monkeypatch.delenv("LLM_PROXY_DEV_MODE", raising=False)
    cfg = {"server": {"auth": {"enabled": True}}}
    apply_dev_mode(cfg)

    assert auth_enabled(cfg) is True


def test_dev_mode_says_so(monkeypatch, caplog):
    """Running open must be visible in the log, not inferred from behaviour."""
    from proxy.config_loader import apply_dev_mode

    monkeypatch.setenv("LLM_PROXY_DEV_MODE", "on")
    with caplog.at_level("WARNING"):
        apply_dev_mode({"server": {"auth": {"enabled": True}}})

    assert any("LLM_PROXY_DEV_MODE" in r.message for r in caplog.records)


def test_a_missing_config_still_fails_closed(tmp_path, monkeypatch):
    """The pre-existing guarantee, kept: no file means authenticate."""
    from core.auth_policy import auth_enabled
    from proxy.config_loader import load_config

    monkeypatch.delenv("LLM_PROXY_DEV_MODE", raising=False)
    cfg = load_config(str(tmp_path / "does-not-exist.yaml"))

    assert auth_enabled(cfg) is True


# ── the unsegregated control plane must announce itself ─────────────────────


def _minimal_authenticated_config() -> dict:
    return {
        "server": {
            "auth": {
                "enabled": True,
                "api_keys_env": "LLM_PROXY_TEST_INFERENCE",
                "admin_keys_env": "LLM_PROXY_TEST_ADMIN",
            },
            "port": 8090,
        },
        "endpoints": {},
    }


def test_an_unset_admin_bag_warns(monkeypatch):
    """Falling back to the inference keys is defensible; doing it silently is not."""
    from core.startup_checks import validate_config

    monkeypatch.setenv("LLM_PROXY_TEST_INFERENCE", "sk-proxy-abc")
    monkeypatch.delenv("LLM_PROXY_TEST_ADMIN", raising=False)

    warnings = validate_config(_minimal_authenticated_config())

    assert any("LLM_PROXY_TEST_ADMIN" in w for w in warnings), (
        f"no warning names the admin key variable: {warnings}"
    )


def test_a_configured_admin_bag_does_not_warn(monkeypatch):
    from core.startup_checks import validate_config

    monkeypatch.setenv("LLM_PROXY_TEST_INFERENCE", "sk-proxy-abc")
    monkeypatch.setenv("LLM_PROXY_TEST_ADMIN", "sk-admin-def")

    warnings = validate_config(_minimal_authenticated_config())

    assert not any("LLM_PROXY_TEST_ADMIN" in w for w in warnings)


def test_no_admin_warning_when_auth_is_off(monkeypatch):
    """With auth disabled the tier is moot — do not add noise to dev runs."""
    from core.startup_checks import validate_config

    monkeypatch.delenv("LLM_PROXY_TEST_ADMIN", raising=False)
    cfg = _minimal_authenticated_config()
    cfg["server"]["auth"]["enabled"] = False

    warnings = validate_config(cfg)

    assert not any("LLM_PROXY_TEST_ADMIN" in w for w in warnings)


# ── and the whole point: the default deployment refuses an anonymous caller ──


@pytest.mark.asyncio
async def test_the_shipped_config_refuses_an_anonymous_control_plane_read(
    monkeypatch,
):
    """End to end, with the real middleware, using the file that ships."""
    import httpx

    from conftest import InMemoryRepository
    from core.infisical import clear_cache
    from proxy.app_factory import create_app
    from test_e2e import LightweightAgent

    monkeypatch.setenv("LLM_PROXY_API_KEYS", "sk-proxy-shipped")
    monkeypatch.delenv("LLM_PROXY_DEV_MODE", raising=False)
    clear_cache()

    shipped = _load("config.yaml")
    # Keep the shipped auth block; drop the rest so no provider is contacted.
    cfg = {
        "server": shipped["server"],
        "endpoints": {},
        "security": {"enabled": False},
    }
    agent = LightweightAgent(InMemoryRepository(), cfg)
    agent.app = create_app(agent)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=agent.app), base_url="http://test"
    ) as c:
        for path in ("/api/v1/registry", "/api/v1/config/raw", "/v1/models"):
            resp = await c.get(path)
            assert resp.status_code == 401, (
                f"{path} answered {resp.status_code} without a credential under "
                "the shipped configuration"
            )
