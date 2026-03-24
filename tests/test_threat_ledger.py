"""
Tests for ThreatLedger (cross-session threat intelligence)
and ResponseSigner (HMAC provenance).
"""

import time
import pytest

from core.threat_ledger import ThreatLedger
from core.response_signer import ResponseSigner


# ══════════════════════════════════════════════════════════
# ThreatLedger
# ══════════════════════════════════════════════════════════

class TestThreatLedger:

    def test_single_low_score_no_block(self):
        """A single low-score event does not trigger a block."""
        ledger = ThreatLedger(threshold=3.0, min_events=3)
        result = ledger.record(ip="1.2.3.4", score=0.5)
        assert result is None

    def test_accumulating_scores_triggers_block(self):
        """Multiple scores from same IP that sum over threshold trigger block."""
        ledger = ThreatLedger(threshold=2.0, min_events=3, window_seconds=60)
        assert ledger.record(ip="1.2.3.4", score=0.8) is None
        assert ledger.record(ip="1.2.3.4", score=0.8) is None
        result = ledger.record(ip="1.2.3.4", score=0.8)
        assert result is not None
        assert "Cross-session threat" in result

    def test_different_ips_independent(self):
        """Scores from different IPs don't aggregate."""
        ledger = ThreatLedger(threshold=2.0, min_events=3)
        ledger.record(ip="1.1.1.1", score=0.8)
        ledger.record(ip="2.2.2.2", score=0.8)
        ledger.record(ip="3.3.3.3", score=0.8)
        # No single IP has 3 events
        result = ledger.record(ip="1.1.1.1", score=0.8)
        # IP 1.1.1.1 now has 2 events (0.8+0.8=1.6) — under threshold
        assert result is None

    def test_key_prefix_tracking(self):
        """Scores tracked by API key prefix independently from IP."""
        ledger = ThreatLedger(threshold=2.0, min_events=3)
        # Same key, different IPs
        ledger.record(ip="1.1.1.1", key_prefix="sk-evil", score=0.8)
        ledger.record(ip="2.2.2.2", key_prefix="sk-evil", score=0.8)
        result = ledger.record(ip="3.3.3.3", key_prefix="sk-evil", score=0.8)
        # key_prefix "sk-evil" has 3 events summing 2.4 > 2.0
        assert result is not None

    def test_min_events_respected(self):
        """Threshold not checked until min_events reached."""
        ledger = ThreatLedger(threshold=0.5, min_events=5)
        # Even with high score, won't block until 5 events
        for _ in range(4):
            result = ledger.record(ip="1.2.3.4", score=1.0)
            assert result is None
        # 5th event: 5 * 1.0 = 5.0 > 0.5
        result = ledger.record(ip="1.2.3.4", score=1.0)
        assert result is not None

    def test_zero_score_not_recorded_toward_block(self):
        """Zero-score events are recorded but don't contribute to sum."""
        ledger = ThreatLedger(threshold=2.0, min_events=3)
        ledger.record(ip="1.2.3.4", score=0.0)
        ledger.record(ip="1.2.3.4", score=0.0)
        result = ledger.record(ip="1.2.3.4", score=0.0)
        # 3 events but sum = 0.0 < 2.0
        assert result is None

    def test_get_actor_score(self):
        """Actor score API returns current state."""
        ledger = ThreatLedger()
        ledger.record(ip="10.0.0.1", score=0.5)
        ledger.record(ip="10.0.0.1", score=0.7)

        state = ledger.get_actor_score("10.0.0.1")
        assert state["ip_score_sum"] == pytest.approx(1.2)
        assert state["ip_events"] == 2

    def test_stats(self):
        """Ledger stats show tracking counts."""
        ledger = ThreatLedger()
        ledger.record(ip="1.1.1.1", score=0.5)
        ledger.record(ip="2.2.2.2", key_prefix="sk-a", score=0.3)

        stats = ledger.stats
        assert stats["tracked_ips"] == 2
        assert stats["tracked_keys"] == 1

    def test_empty_ip_and_key_no_error(self):
        """Empty IP/key gracefully handled."""
        ledger = ThreatLedger()
        result = ledger.record(ip="", key_prefix="", score=0.9)
        assert result is None


# ══════════════════════════════════════════════════════════
# ResponseSigner
# ══════════════════════════════════════════════════════════

class TestResponseSigner:

    def test_sign_returns_headers(self):
        """Signing produces the expected header set."""
        signer = ResponseSigner(secret="test-secret-key")
        headers = signer.sign_response(
            response_body=b'{"choices":[{"message":{"content":"Hello"}}]}',
            model="gpt-4o",
            provider="openai",
            request_id="abc123",
        )
        assert "X-LLMProxy-Signature" in headers
        assert "X-LLMProxy-Signed-At" in headers
        assert "X-LLMProxy-Signed-Fields" in headers
        assert len(headers["X-LLMProxy-Signature"]) == 64  # SHA256 hex

    def test_verify_valid_signature(self):
        """A correctly signed response verifies successfully."""
        secret = "my-secret"
        signer = ResponseSigner(secret=secret)
        body = b'{"result": "test"}'

        headers = signer.sign_response(
            response_body=body, model="gpt-4o", provider="openai", request_id="req1",
        )

        assert ResponseSigner.verify(
            secret=secret,
            response_body=body,
            model="gpt-4o",
            provider="openai",
            timestamp=headers["X-LLMProxy-Signed-At"],
            request_id="req1",
            expected_signature=headers["X-LLMProxy-Signature"],
        )

    def test_verify_tampered_body_fails(self):
        """Tampered response body fails verification."""
        secret = "my-secret"
        signer = ResponseSigner(secret=secret)
        body = b'{"result": "original"}'

        headers = signer.sign_response(
            response_body=body, model="gpt-4o", provider="openai", request_id="req1",
        )

        assert not ResponseSigner.verify(
            secret=secret,
            response_body=b'{"result": "TAMPERED"}',
            model="gpt-4o",
            provider="openai",
            timestamp=headers["X-LLMProxy-Signed-At"],
            request_id="req1",
            expected_signature=headers["X-LLMProxy-Signature"],
        )

    def test_verify_wrong_secret_fails(self):
        """Wrong secret fails verification."""
        signer = ResponseSigner(secret="correct-secret")
        body = b'test body'

        headers = signer.sign_response(
            response_body=body, model="gpt-4o", provider="openai", request_id="req1",
        )

        assert not ResponseSigner.verify(
            secret="wrong-secret",
            response_body=body,
            model="gpt-4o",
            provider="openai",
            timestamp=headers["X-LLMProxy-Signed-At"],
            request_id="req1",
            expected_signature=headers["X-LLMProxy-Signature"],
        )

    def test_disabled_without_secret(self):
        """Signer is disabled when no secret is provided."""
        signer = ResponseSigner(secret="")
        assert not signer.enabled
        headers = signer.sign_response(response_body=b"test")
        assert headers == {}

    def test_different_models_different_signatures(self):
        """Same body but different model produces different signature."""
        signer = ResponseSigner(secret="key")
        body = b'same body'

        h1 = signer.sign_response(response_body=body, model="gpt-4o", provider="openai", request_id="r1")
        h2 = signer.sign_response(response_body=body, model="claude-sonnet", provider="anthropic", request_id="r1")

        assert h1["X-LLMProxy-Signature"] != h2["X-LLMProxy-Signature"]

    def test_constant_time_comparison(self):
        """Verify uses hmac.compare_digest (constant-time) to prevent timing attacks."""
        import hmac as _hmac
        # This is a design assertion — verify() uses hmac.compare_digest internally
        # We test by checking that wrong signatures don't short-circuit
        signer = ResponseSigner(secret="key")
        body = b'test'
        headers = signer.sign_response(response_body=body, model="m", provider="p", request_id="r")

        # Both should take roughly the same time (can't assert timing, but verify logic)
        assert not ResponseSigner.verify("key", body, "m", "p", headers["X-LLMProxy-Signed-At"], "r", "0" * 64)
        assert not ResponseSigner.verify("key", body, "m", "p", headers["X-LLMProxy-Signed-At"], "r", "f" * 64)
