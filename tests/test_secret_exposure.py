"""Secrets do not leave through the "redacted" config view or the logs.

* ``scrub_dict`` hid only the exact names on a short list (``secret``,
  ``password``...), so ``jwt_secret``, ``signing_secret``, a database DSN and
  ``redis_url`` came back verbatim from the config view that is called redacted.
* The Redis URL, which carries the password when one is set (the compose file
  documents ``redis://:password@redis:6379``), was logged at INFO by three
  start-up paths and returned by the rate-limit config endpoint.
"""

import pytest

from core.export import scrub_dict, scrub_pii
from core.redis_client import redact_url


@pytest.mark.parametrize(
    "url, expected",
    [
        ("redis://:s3cret@redis:6379/0", "redis://:***@redis:6379/0"),
        ("redis://bob:pw@[::1]:6380", "redis://bob:***@[::1]:6380"),
        ("rediss://u:p%40ss@h/1", "rediss://u:***@h/1"),
        ("redis://redis:6379", "redis://redis:6379"),
        ("redis://user@redis:6379", "redis://user@redis:6379"),
        (None, None),
        ("", ""),
    ],
)
def test_redact_url_hides_only_the_password(url, expected):
    assert redact_url(url) == expected
    if url and "s3cret" in url:
        assert "s3cret" not in redact_url(url)


def test_the_config_view_hides_secrets_by_what_their_names_say():
    cfg = {
        "security": {"sse": {"signing_secret": "A", "max_tokens": 5}},
        "identity": {"jwt_secret": "B", "session_ttl": 3600},
        "database_dsn": "postgresql://u:C@db/x",
        "caching": {"redis_url": "redis://:D@redis:6379", "redis_password": "E"},
        "webhooks": [{"url": "https://hook.example.com/x", "token": "F"}],
    }

    flat = repr(scrub_dict(cfg))

    for secret in "ABCDEF":
        assert f"'{secret}'" not in flat and f":{secret}@" not in flat, secret
    # What an operator needs to read is still there.
    assert "'max_tokens': 5" in flat
    assert "'session_ttl': 3600" in flat
    assert "redis:6379" in flat


def test_a_name_that_points_at_an_environment_variable_stays_readable():
    out = scrub_dict({"endpoints": {"openai": {"api_key_env": "OPENAI_API_KEY"}}})

    assert out["endpoints"]["openai"]["api_key_env"] == "OPENAI_API_KEY"


def test_credentials_in_a_url_are_not_mistaken_for_an_email_address():
    # "pw@host.com" matches the e-mail pattern; the URL pattern has to win first.
    assert scrub_pii("postgres://app:pw@db.example.com/x") == "postgres://<CREDENTIALS>@db.example.com/x"
    assert scrub_pii("write to ada@example.com") == "write to <EMAIL>"
