"""An unavailable Infisical is noticed once, not on every secret lookup.

With the SDK missing (or no credentials, the default in .env.example) every
get_secret call retried the import and logged the same warning again: 5,000 lookups
meant 5,000 warnings, on a path a request reaches.
"""

import logging

import pytest

from core import infisical


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(infisical, "_client", None)
    monkeypatch.setattr(infisical, "_unavailable", False, raising=False)
    monkeypatch.delenv("INFISICAL_CLIENT_ID", raising=False)
    monkeypatch.delenv("INFISICAL_CLIENT_SECRET", raising=False)
    infisical.clear_cache()


def test_missing_credentials_warn_once(caplog, monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "infisical_sdk", type("M", (), {"InfisicalSDKClient": object}))
    monkeypatch.delenv("UNSET_SECRET", raising=False)

    # An unresolved secret is never cached, so each lookup reaches the client check.
    with caplog.at_level(logging.WARNING, logger=infisical.logger.name):
        values = {infisical.get_secret("UNSET_SECRET") for _ in range(200)}

    assert values == {None}
    assert sum("INFISICAL_CLIENT_ID" in r.message for r in caplog.records) == 1


def test_a_set_environment_secret_is_still_found_after_the_client_is_given_up_on(monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "infisical_sdk", None)
    monkeypatch.setenv("SOME_SECRET", "from-env")

    assert infisical.get_secret("SOME_SECRET") == "from-env"
    assert infisical.get_secret("SOME_SECRET") == "from-env"


def test_a_missing_sdk_warns_once(caplog, monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "infisical_sdk", None)  # import raises

    with caplog.at_level(logging.WARNING, logger=infisical.logger.name):
        for _ in range(200):
            infisical.get_secret("NOPE", required=False)

    assert sum("not installed" in r.message for r in caplog.records) == 1


def test_clear_cache_lets_a_rotation_retry(monkeypatch):
    infisical._unavailable = True

    infisical.clear_cache()

    assert infisical._unavailable is False
