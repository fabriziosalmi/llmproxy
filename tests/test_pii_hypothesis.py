"""
Property-based tests for PII detection and masking in core/security.py.

Uses Hypothesis to verify that:
- Known PII patterns (emails, phones, SSNs, credit cards, IBANs) are always detected
- Random alphanumeric strings without PII structure are NOT detected
- mask_pii always replaces detected PII with vault tokens (never returns raw PII)
- demask_pii(mask_pii(text)) roundtrips correctly
"""

import re
import pytest
from hypothesis import given, strategies as st, settings, assume

from core.security import SecurityShield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_shield() -> SecurityShield:
    """Create a SecurityShield with default config and enabled."""
    return SecurityShield({"security": {"enabled": True}})


# Strategies for generating valid PII values

email_strategy = st.from_regex(
    r"[a-z][a-z0-9]{2,8}@[a-z]{3,8}\.(com|org|net)", fullmatch=True
)

phone_strategy = st.from_regex(
    r"[2-9]\d{2}-[2-9]\d{2}-\d{4}", fullmatch=True
)

ssn_strategy = st.from_regex(
    r"[1-8]\d{2}-\d{2}-\d{4}", fullmatch=True
)

credit_card_strategy = st.from_regex(
    r"4\d{3} \d{4} \d{4} \d{4}", fullmatch=True
)

iban_strategy = st.from_regex(
    r"DE\d{2} \d{4} \d{4} \d{4} \d{4} \d{2}", fullmatch=True
)


# Strategy for alphanumeric strings that should NOT match PII patterns.
# Avoid digits-only sequences longer than 8 chars and anything with @ or -.
safe_alphanum_strategy = st.from_regex(r"[A-Za-z]{5,20}", fullmatch=True)


# ---------------------------------------------------------------------------
# Tests: Detection
# ---------------------------------------------------------------------------

class TestPIIDetection:
    """Verify that _check_pii_leak and _REGEX_PII_PATTERNS detect known PII."""

    @given(email=email_strategy)
    @settings(max_examples=50, deadline=None)
    def test_emails_always_detected(self, email):
        shield = _make_shield()
        text = f"Please contact {email} for details."
        assert shield._check_pii_leak(text), f"Email not detected: {email}"

    @given(phone=phone_strategy)
    @settings(max_examples=50, deadline=None)
    def test_phones_always_detected(self, phone):
        shield = _make_shield()
        text = f"Call me at {phone} anytime."
        assert shield._check_pii_leak(text), f"Phone not detected: {phone}"

    @given(ssn=ssn_strategy)
    @settings(max_examples=50, deadline=None)
    def test_ssns_always_detected(self, ssn):
        shield = _make_shield()
        text = f"My SSN is {ssn}."
        assert shield._check_pii_leak(text), f"SSN not detected: {ssn}"

    @given(cc=credit_card_strategy)
    @settings(max_examples=50, deadline=None)
    def test_credit_cards_always_detected(self, cc):
        shield = _make_shield()
        text = f"Card number: {cc}"
        assert shield._check_pii_leak(text), f"Credit card not detected: {cc}"

    @given(iban=iban_strategy)
    @settings(max_examples=50, deadline=None)
    def test_ibans_always_detected(self, iban):
        shield = _make_shield()
        text = f"Transfer to {iban}."
        assert shield._check_pii_leak(text), f"IBAN not detected: {iban}"

    @given(text=safe_alphanum_strategy)
    @settings(max_examples=100, deadline=None)
    def test_safe_alphanumeric_not_detected(self, text):
        """Pure alphabetic strings should not trigger PII detection."""
        shield = _make_shield()
        assert not shield._check_pii_leak(text), f"False positive PII on: {text}"


# ---------------------------------------------------------------------------
# Tests: Masking
# ---------------------------------------------------------------------------

class TestPIIMasking:
    """Verify mask_pii replaces PII with vault tokens."""

    @given(email=email_strategy)
    @settings(max_examples=50, deadline=None)
    def test_mask_email_produces_token(self, email):
        shield = _make_shield()
        text = f"Email: {email}"
        masked = shield.mask_pii(text)
        assert email not in masked, f"Raw email still present after masking: {email}"
        assert "[PII_EMAIL_" in masked, "Vault token not found in masked output"

    @given(phone=phone_strategy)
    @settings(max_examples=50, deadline=None)
    def test_mask_phone_produces_token(self, phone):
        shield = _make_shield()
        text = f"Phone: {phone}"
        masked = shield.mask_pii(text)
        assert phone not in masked, f"Raw phone still present after masking: {phone}"
        assert "[PII_PHONE_" in masked, "Vault token not found in masked output"

    @given(ssn=ssn_strategy)
    @settings(max_examples=50, deadline=None)
    def test_mask_ssn_produces_token(self, ssn):
        shield = _make_shield()
        text = f"SSN: {ssn}"
        masked = shield.mask_pii(text)
        assert ssn not in masked, f"Raw SSN still present after masking: {ssn}"
        assert "[PII_SSN_" in masked, "Vault token not found in masked output"

    @given(cc=credit_card_strategy)
    @settings(max_examples=50, deadline=None)
    def test_mask_credit_card_produces_token(self, cc):
        shield = _make_shield()
        text = f"Card: {cc}"
        masked = shield.mask_pii(text)
        assert cc not in masked, f"Raw CC still present after masking: {cc}"
        assert "[PII_CREDIT_CARD_" in masked, "Vault token not found in masked output"

    def test_mask_empty_string(self):
        shield = _make_shield()
        assert shield.mask_pii("") == ""

    def test_mask_no_pii(self):
        shield = _make_shield()
        text = "Hello, how are you today?"
        assert shield.mask_pii(text) == text


# ---------------------------------------------------------------------------
# Tests: Roundtrip (mask -> demask)
# ---------------------------------------------------------------------------

class TestPIIRoundtrip:
    """Verify that demask_pii(mask_pii(text)) restores the original text."""

    @given(email=email_strategy)
    @settings(max_examples=50, deadline=None)
    def test_roundtrip_email(self, email):
        shield = _make_shield()
        original = f"Contact {email} for info."
        masked = shield.mask_pii(original)
        restored = shield.demask_pii(masked)
        assert restored == original, f"Roundtrip failed: {original!r} -> {masked!r} -> {restored!r}"

    @given(phone=phone_strategy)
    @settings(max_examples=50, deadline=None)
    def test_roundtrip_phone(self, phone):
        shield = _make_shield()
        original = f"Call {phone} now."
        masked = shield.mask_pii(original)
        restored = shield.demask_pii(masked)
        assert restored == original, f"Roundtrip failed: {original!r} -> {masked!r} -> {restored!r}"

    @given(ssn=ssn_strategy)
    @settings(max_examples=50, deadline=None)
    def test_roundtrip_ssn(self, ssn):
        shield = _make_shield()
        original = f"SSN is {ssn}."
        masked = shield.mask_pii(original)
        restored = shield.demask_pii(masked)
        assert restored == original, f"Roundtrip failed: {original!r} -> {masked!r} -> {restored!r}"

    @given(cc=credit_card_strategy)
    @settings(max_examples=50, deadline=None)
    def test_roundtrip_credit_card(self, cc):
        shield = _make_shield()
        original = f"Card: {cc}."
        masked = shield.mask_pii(original)
        restored = shield.demask_pii(masked)
        assert restored == original, f"Roundtrip failed: {original!r} -> {masked!r} -> {restored!r}"

    def test_roundtrip_no_pii(self):
        """Text without PII should pass through mask/demask unchanged."""
        shield = _make_shield()
        original = "No sensitive data here."
        masked = shield.mask_pii(original)
        restored = shield.demask_pii(masked)
        assert restored == original

    def test_roundtrip_multiple_pii(self):
        """Multiple PII values in one string should all roundtrip correctly."""
        shield = _make_shield()
        original = "Email: foo@bar.com, SSN: 123-45-6789, Phone: 555-123-4567"
        masked = shield.mask_pii(original)
        # Verify all PII is masked
        assert "foo@bar.com" not in masked
        assert "123-45-6789" not in masked
        assert "555-123-4567" not in masked
        # Roundtrip
        restored = shield.demask_pii(masked)
        assert restored == original
