"""
PII Detection Tests — validates regex patterns and vault-based masking/demasking.

Tests the SecurityShield PII layer regardless of whether Presidio is installed.
"""

import pytest
from core.security import SecurityShield, _PRESIDIO_AVAILABLE


@pytest.fixture
def shield():
    config = {"security": {"enabled": True}}
    return SecurityShield(config)


# ── Detection ──

def test_detects_email(shield):
    assert shield._check_pii_leak("Contact me at john@example.com please") is True


def test_detects_phone(shield):
    assert shield._check_pii_leak("Call me at 555-123-4567") is True


def test_detects_ssn(shield):
    assert shield._check_pii_leak("My SSN is 123-45-6789") is True


def test_no_pii_clean_text(shield):
    assert shield._check_pii_leak("The weather is nice today") is False


def test_detects_credit_card(shield):
    result = shield._check_pii_leak("Card: 4111 1111 1111 1111")
    # Regex fallback detects this; Presidio definitely detects it
    assert result is True


# ── Masking ──

def test_mask_email(shield):
    text = "Send to alice@corp.com"
    masked = shield.mask_pii(text)
    assert "alice@corp.com" not in masked
    assert "[PII_" in masked


def test_mask_phone(shield):
    text = "Call 555-867-5309 now"
    masked = shield.mask_pii(text)
    assert "555-867-5309" not in masked
    assert "[PII_" in masked


def test_mask_preserves_non_pii(shield):
    text = "Hello world, no PII here"
    assert shield.mask_pii(text) == text


# ── Vault & Demasking ──

def test_vault_stores_originals(shield):
    text = "Email: test@example.org"
    shield.mask_pii(text)
    assert any("test@example.org" in v for v in shield.pii_vault.values())


def test_demask_restores_originals(shield):
    original = "My email is bob@test.com and my phone is 555-111-2222"
    masked = shield.mask_pii(original)
    restored = shield.demask_pii(masked)
    assert "bob@test.com" in restored
    assert "555-111-2222" in restored


def test_disabled_shield_passthrough(shield):
    shield.enabled = False
    text = "secret@email.com 123-45-6789"
    assert shield.mask_pii(text) == text
    assert shield.demask_pii(text) == text


# ── Multiple PII in one text ──

def test_mask_multiple_pii(shield):
    text = "alice@a.com and bob@b.com and 555-000-1111"
    masked = shield.mask_pii(text)
    assert "alice@a.com" not in masked
    assert "bob@b.com" not in masked
    assert "555-000-1111" not in masked
    # All should be in vault
    assert len(shield.pii_vault) >= 3


# ── Presidio availability marker ──

def test_presidio_availability_flag():
    """Verify the _PRESIDIO_AVAILABLE flag is a bool (either path works)."""
    assert isinstance(_PRESIDIO_AVAILABLE, bool)
