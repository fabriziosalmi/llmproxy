"""The response link sanitiser must fail closed.

Its per-URL handler wraps three security checks — blocked-domain matching,
brand-impersonation detection and FQDN risk scoring — in one try. That try
used to end in `except Exception: pass` followed by `return match.group(0)`,
the unmodified URL. So any error inside turned a block into a pass, on the
response path, with no log.

The reachable trigger was not exotic. Domain matching returns on the first
match, so a single non-string entry placed before the real ones raised
AttributeError on d.lower() and aborted matching for that URL. A blank list
item in YAML parses as None and an unquoted number parses as an int, so an
ordinary config typo disabled link blocking for every URL in every response.
"""

import pytest

from core.security import SecurityShield

TEXT = "see http://evil.com/x for details"


def _shield(link_cfg):
    return SecurityShield(
        {
            "security": {
                "link_sanitization": link_cfg,
                "language_guard": {"enabled": False},
                "injection_guard": {"enabled": False},
            }
        }
    )


@pytest.fixture(autouse=True)
def _reset_warning_latch():
    """The unusable-entry warning fires once per class; reset between tests."""
    SecurityShield._blocked_domains_warned = False
    yield
    SecurityShield._blocked_domains_warned = False


# ── a malformed entry must not disable the whole list ───────────────────────


@pytest.mark.parametrize(
    "blocked",
    [
        ["evil.com"],
        ["evil.com", 1337],
        [1337, "evil.com"],
        [None, "evil.com"],
        ["", "evil.com"],
        ["   ", "evil.com"],
        [None, 1337, "evil.com"],
    ],
)
def test_a_bad_entry_does_not_disable_domain_blocking(blocked):
    out = _shield({"blocked_domains": blocked}).sanitize_response(TEXT)
    assert "[BLOCKED_LINK]" in out, (
        f"blocked_domains={blocked!r} let evil.com through; one unusable entry "
        "must not disable matching for the rest"
    )
    assert "evil.com" not in out


def test_unusable_entries_are_reported_to_the_operator(caplog):
    with caplog.at_level("ERROR"):
        _shield({"blocked_domains": [None, "evil.com"]}).sanitize_response(TEXT)
    assert any("blocked_domains" in r.message for r in caplog.records), (
        "dropping a config entry silently is what made this invisible"
    )


# ── an unexpected error blocks rather than passes ───────────────────────────


def test_an_unexpected_error_blocks_the_link(monkeypatch, caplog):
    """The general property: a check that could not complete must not pass."""
    shield = _shield({"blocked_domains": ["something-else.test"]})

    def _boom(netloc):
        raise RuntimeError("brand table unavailable")

    monkeypatch.setattr(shield, "_homograph_brand", _boom)
    with caplog.at_level("ERROR"):
        out = shield.sanitize_response(TEXT)

    assert "[BLOCKED_LINK]" in out, "a failed security check must fail closed"
    assert "evil.com" not in out
    assert any("Link sanitizer failed" in r.message for r in caplog.records), (
        "the failure must be diagnosable; it previously emitted nothing"
    )


def test_fail_open_is_available_but_opt_in(monkeypatch, caplog):
    """An operator may prefer availability — explicitly, and still logged."""
    shield = _shield({"blocked_domains": ["something-else.test"], "fail_open": True})
    monkeypatch.setattr(
        shield, "_homograph_brand", lambda n: (_ for _ in ()).throw(RuntimeError("x"))
    )
    with caplog.at_level("ERROR"):
        out = shield.sanitize_response(TEXT)

    assert "http://evil.com/x" in out
    assert any("Link sanitizer failed" in r.message for r in caplog.records)


# ── the normal paths still work ─────────────────────────────────────────────


def test_an_unlisted_domain_is_untouched():
    out = _shield({"blocked_domains": ["evil.com"]}).sanitize_response(
        "see http://example.com/ok for details"
    )
    assert "http://example.com/ok" in out


def test_subdomains_of_a_blocked_domain_are_blocked():
    out = _shield({"blocked_domains": ["evil.com"]}).sanitize_response(
        "see http://a.b.evil.com/x for details"
    )
    assert "[BLOCKED_LINK]" in out
