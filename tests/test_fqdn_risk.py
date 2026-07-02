"""Tests for the offline lexical FQDN risk scorer (core/fqdn_risk.py) — the
network-free convergence with fqdn-model — and its wiring into the SecurityShield
link sanitizer.

The two guarantees that matter: (1) ordinary/benign hosts stay well below the
block threshold (low false positives), and (2) realistic phishing/typosquat hosts
that combine a risky TLD with phishing keywords and obfuscation clear it."""
import pytest

from core.fqdn_risk import (
    DEFAULT_BLOCK_THRESHOLD,
    assess,
    features,
    _parse_ip,
    _subdomain_count,
)
from core.security import SecurityShield


# ── Benign corpus: must stay below the block threshold ───────────────────────
BENIGN = [
    "google.com",
    "github.com",
    "raw.githubusercontent.com",
    "cloud.google.com",
    "mail.google.com",
    "s3.amazonaws.com",
    "en.wikipedia.org",
    "cdn.jsdelivr.net",
    "api.stripe.com",
    "foo.co.uk",
    # legit despite containing a "login" keyword — must NOT be blocked
    "login.microsoftonline.com",
]


@pytest.mark.parametrize("host", BENIGN)
def test_benign_hosts_below_threshold(host):
    score, _ = assess(host)
    assert score < DEFAULT_BLOCK_THRESHOLD, f"{host} scored {score}"


def test_plain_apex_domains_score_zero():
    for host in ("google.com", "github.com", "stripe.com"):
        assert assess(host)[0] == 0.0


# ── Malicious/phishing corpus: must clear the threshold ──────────────────────
PHISHING = [
    "secure-login-verify.xyz",
    "paypa1-secure-account-update.top",
    "account-verify-login-secure-bank.click",
    "free-download-admin-panel.loan",
]


@pytest.mark.parametrize("host", PHISHING)
def test_phishing_hosts_flagged(host):
    score, reasons = assess(host)
    assert score >= DEFAULT_BLOCK_THRESHOLD, f"{host} scored {score} ({reasons})"


# ── Individual features ──────────────────────────────────────────────────────
def test_risky_tld_feature():
    assert "risky_tld" in features("something.xyz")
    assert "risky_tld" not in features("something.com")


def test_shortener_feature():
    assert "shortener" in features("bit.ly")
    assert "shortener" not in features("notbit.ly.com")


def test_suspicious_keyword_feature():
    assert "suspicious_keyword" in features("secure-bank.com")
    assert "suspicious_keyword" not in features("example.com")


def test_excess_hyphens_and_digits():
    assert "excess_hyphens" in features("a-b-c-d.com")  # 3 hyphens > 1
    assert "excess_hyphens" not in features("a-b.com")  # 1 hyphen ok
    assert "excess_digits" in features("h0st12345.com")  # 6 digits > 4
    assert "excess_digits" not in features("h0st.com")  # 1 digit ok


def test_ip_literal_detection_all_forms():
    # M3: dotted IPv4, IPv6 (bracketed/bare), decimal and hex integer literals.
    assert _parse_ip("192.168.1.1") is not None
    assert _parse_ip("[::1]") is not None
    assert _parse_ip("::1") is not None
    assert _parse_ip("2130706433") is not None  # decimal for 127.0.0.1
    assert _parse_ip("0x7f000001") is not None  # hex for 127.0.0.1
    assert _parse_ip("256.1.1.1") is None  # invalid octet
    assert _parse_ip("example.com") is None


def test_obfuscated_loopback_ip_literals_are_flagged():
    # M3: the forms that previously bypassed ip_as_host entirely now flag as
    # private/loopback and clear the threshold (SSRF/exfil bait in a link).
    for host in ["127.0.0.1", "2130706433", "0x7f000001", "::1", "192.168.100.200", "169.254.1.1"]:
        score, reasons = assess(host)
        assert "ip_as_host" in reasons and "private_ip" in reasons, host
        assert score >= DEFAULT_BLOCK_THRESHOLD, f"{host} scored {score}"


def test_public_ip_literal_is_mild_not_blocked():
    score, reasons = assess("185.100.50.5")
    assert reasons == ["ip_as_host"]
    assert score < DEFAULT_BLOCK_THRESHOLD  # public IP alone is only mildly suspicious


def test_keyword_hits_escalate():
    # M4: multiple phishing keywords accumulate (capped), so realistic .com
    # phishing clears the threshold where a single keyword does not.
    assert assess("paypal-login-verify-account.com")[0] >= DEFAULT_BLOCK_THRESHOLD
    # ...but a lone keyword on a legitimate host stays well below.
    assert assess("login.microsoftonline.com")[0] < DEFAULT_BLOCK_THRESHOLD


def test_punycode_is_a_signal():
    assert "punycode" in features("xn--pypal-4ve.com")


def test_subdomain_count_handles_multilevel_tld():
    assert _subdomain_count("foo.co.uk") == 0  # co.uk is the eTLD
    assert _subdomain_count("a.b.c.d.example.com") == 4
    assert _subdomain_count("www.example.com") == 0  # leading www excluded
    assert _subdomain_count("example.com") == 0


def test_empty_and_garbage_hosts_are_safe():
    assert assess("")[0] == 0.0
    assert assess("   ")[0] == 0.0
    assert features("") == []


# ── Wiring into SecurityShield ───────────────────────────────────────────────
def _shield(risk_scoring):
    return SecurityShield(
        {
            "security": {
                "enabled": True,
                "link_sanitization": {"enabled": True, "risk_scoring": risk_scoring},
            }
        }
    )


def test_check_links_blocks_high_risk_when_enabled():
    shield = _shield({"enabled": True, "block_threshold": 0.7})
    err = shield._check_links("please visit http://secure-login-verify.xyz/path now")
    assert err is not None
    assert "risk=" in err


def test_check_links_passes_benign_when_enabled():
    shield = _shield({"enabled": True, "block_threshold": 0.7})
    assert shield._check_links("see https://raw.githubusercontent.com/a/b for code") is None


def test_check_links_risk_scoring_off_by_default():
    shield = _shield({})  # risk_scoring absent → disabled
    assert shield._check_links("http://secure-login-verify.xyz/x") is None


def test_check_links_log_only_does_not_block():
    shield = _shield({"enabled": True, "block_threshold": 0.7, "log_only": True})
    assert shield._check_links("http://secure-login-verify.xyz/x") is None


def test_sanitize_response_replaces_high_risk_link():
    shield = _shield({"enabled": True, "block_threshold": 0.7})
    out = shield.sanitize_response("Click http://paypa1-secure-account-update.top/go here.")
    assert "[BLOCKED_LINK]" in out
    assert "paypa1-secure-account-update.top" not in out


def test_sanitize_response_keeps_benign_link():
    shield = _shield({"enabled": True, "block_threshold": 0.7})
    out = shield.sanitize_response("Docs at https://api.stripe.com/v1 are here.")
    assert "api.stripe.com" in out
    assert "[BLOCKED_LINK]" not in out
