"""One question, one answer: is authentication on?

`server.auth.enabled` was read in thirteen places with two different defaults.
The startup validator treated an absent key as True and demanded API keys; the
twelve runtime readers treated the same absent key as False and served
unauthenticated. So a config that omitted the auth section passed validation —
after the operator supplied the keys it asked for — and then ran open.

These tests pin the resolved value, hold the two halves to the same answer, and
fail if a new reader reintroduces its own default.
"""

import pathlib
import re

import pytest

from core.auth_policy import DEFAULT_AUTH_ENABLED, auth_enabled

REPO = pathlib.Path(__file__).resolve().parent.parent


# ── the value itself ────────────────────────────────────────────────────────


def test_absent_section_means_authentication_is_on():
    """The case that used to run open. A security gateway must fail closed."""
    assert auth_enabled({"server": {"port": 8090}, "endpoints": {}}) is True


@pytest.mark.parametrize(
    "config,expected",
    [
        ({"server": {"auth": {"enabled": True}}}, True),
        ({"server": {"auth": {"enabled": False}}}, False),
        ({"server": {"auth": {}}}, True),
        ({"server": {"auth": None}}, True),
        ({"server": {}}, True),
        ({}, True),
        (None, True),
    ],
)
def test_resolution_is_explicit_for_every_shape(config, expected):
    assert auth_enabled(config) is expected


def test_default_is_on():
    assert DEFAULT_AUTH_ENABLED is True


# ── the two halves agree ────────────────────────────────────────────────────


def test_startup_validation_and_runtime_agree_on_a_config_without_auth(monkeypatch):
    """The exact scenario the audit demonstrated.

    Previously: validate_config raised unless LLM_PROXY_API_KEYS was set (it
    read the default as True), and once the operator set it, every runtime
    reader treated auth as disabled. Startup said authenticated, the proxy
    served open. Now both sides read the same resolver.
    """
    from core.startup_checks import validate_config

    monkeypatch.setenv("LLM_PROXY_API_KEYS", "sk-proxy-" + "a" * 32)
    config = {"server": {"port": 8090}, "endpoints": {}}

    validate_config(config)  # must not raise: keys are present
    assert auth_enabled(config) is True, (
        "startup validated this config as authenticated; the runtime must agree"
    )


def test_startup_still_refuses_when_auth_is_on_without_keys(monkeypatch):
    """The fix must not weaken the existing refusal."""
    from core.startup_checks import StartupError, validate_config

    monkeypatch.delenv("LLM_PROXY_API_KEYS", raising=False)
    with pytest.raises(StartupError):
        validate_config({"server": {"port": 8090}, "endpoints": {}})


def test_explicitly_disabled_auth_still_boots_without_keys(monkeypatch):
    """Development mode is a supported choice and must keep working."""
    from core.startup_checks import validate_config

    monkeypatch.delenv("LLM_PROXY_API_KEYS", raising=False)
    config = {"server": {"auth": {"enabled": False}}, "endpoints": {}}
    validate_config(config)
    assert auth_enabled(config) is False


# ── nobody reintroduces a second default ────────────────────────────────────


def test_no_module_reads_the_key_with_its_own_default():
    """The defect was a duplicated default, so the guard is against duplication.

    Any module resolving server.auth.enabled itself can disagree with the
    resolver, which is how the two halves drifted apart in the first place.
    """
    offenders = []
    pattern = re.compile(r'get\(\s*["\']enabled["\']\s*,\s*(True|False)\s*\)')
    for directory in ("core", "proxy", "store", "plugins"):
        for path in (REPO / directory).rglob("*.py"):
            if path.name == "auth_policy.py":
                continue
            for i, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
                if pattern.search(line) and "auth" in line.lower():
                    offenders.append(f"{path.relative_to(REPO)}:{i}: {line.strip()}")
    assert not offenders, (
        "these read server.auth.enabled with their own default instead of "
        "core.auth_policy.auth_enabled:\n  " + "\n  ".join(offenders)
    )
