"""Tests for core.export.DatasetExporter, scrub_pii, scrub_dict."""

import pytest
import json
from core.export import DatasetExporter, scrub_pii, scrub_dict


def test_scrub_email():
    text = "Contact me at john.doe@example.com for details"
    result = scrub_pii(text)
    assert "john.doe@example.com" not in result
    assert "<EMAIL>" in result


def test_scrub_ip():
    text = "Server at 192.168.1.100 is down"
    result = scrub_pii(text)
    assert "192.168.1.100" not in result
    assert "<IP>" in result


def test_scrub_api_key():
    text = "Use key sk-abc123def456ghi789jkl012mno345pqr678stu901vwx"
    result = scrub_pii(text)
    assert "sk-abc123" not in result
    assert "<API_KEY>" in result


def test_scrub_bearer():
    text = "Bearer eyJhbGciOiJIUzI1NiJ9.payload.signature"
    result = scrub_pii(text)
    assert "Bearer <REDACTED>" in result


# ── K.3 PII parity with core/security.py ────────────────────────────
# These were missing from the export scrubber, so GDPR exports could
# leak PII categories that the security shield masks at log time.


def test_scrub_ssn():
    text = "Subject SSN is 123-45-6789 on file"
    result = scrub_pii(text)
    assert "123-45-6789" not in result
    assert "<SSN>" in result


def test_scrub_us_phone():
    text = "Call 555-123-4567 between 9-5"
    result = scrub_pii(text)
    assert "555-123-4567" not in result
    assert "<PHONE>" in result


def test_scrub_intl_phone():
    text = "EU contact: +49 30 12345678"
    result = scrub_pii(text)
    assert "+49 30 12345678" not in result
    assert "<PHONE>" in result


def test_scrub_credit_card_visa():
    text = "Charged card 4111 1111 1111 1111"
    result = scrub_pii(text)
    assert "4111 1111 1111 1111" not in result
    assert "<CREDIT_CARD>" in result


def test_scrub_credit_card_amex():
    text = "Amex 3782 822463 10005 declined"
    result = scrub_pii(text)
    assert "3782 822463 10005" not in result
    assert "<CREDIT_CARD>" in result


def test_scrub_iban():
    text = "Wire to DE89 3704 0044 0532 0130 00"
    result = scrub_pii(text)
    assert "DE89 3704 0044 0532 0130 00" not in result
    assert "<IBAN>" in result


def test_scrub_dict_scrubs_all_pii_categories():
    """Article 15 export safety: nested dicts must scrub every PII category."""
    data = {
        "audit": [
            {"prompt": "Email me at user@example.com or call 555-123-4567"},
            {"metadata": {"ssn": "999-12-3456", "card": "4111 1111 1111 1111"}},
        ],
    }
    result = scrub_dict(data)
    flat = json.dumps(result)
    assert "user@example.com" not in flat
    assert "555-123-4567" not in flat
    assert "999-12-3456" not in flat
    assert "4111 1111 1111 1111" not in flat
    assert "<EMAIL>" in flat
    assert "<PHONE>" in flat
    assert "<SSN>" in flat
    assert "<CREDIT_CARD>" in flat


def test_scrub_dict_sensitive_fields():
    data = {
        "authorization": "Bearer secret-token-xyz",
        "content": "hello world",
    }
    result = scrub_dict(data)
    assert result["authorization"] == "<REDACTED>"
    assert result["content"] == "hello world"


def test_scrub_dict_nested():
    data = {
        "outer": {"token": "secret123", "message": "user@example.com said hi"},
    }
    result = scrub_dict(data)
    assert result["outer"]["token"] == "<REDACTED>"
    assert "<EMAIL>" in result["outer"]["message"]


@pytest.mark.asyncio
async def test_record_creates_file(tmp_path):
    exporter = DatasetExporter(
        output_dir=str(tmp_path), scrub=False, compress_on_rotate=False
    )
    entry = {"prompt": "Hello", "response": "World", "model": "gpt-4"}
    await exporter.record(entry)
    await exporter.close()

    files = list(tmp_path.iterdir())
    assert len(files) >= 1
    content = files[0].read_text().strip()
    record = json.loads(content)
    assert record["model"] == "gpt-4"


@pytest.mark.asyncio
async def test_record_scrubs_pii(tmp_path):
    exporter = DatasetExporter(
        output_dir=str(tmp_path), scrub=True, compress_on_rotate=False
    )
    entry = {
        "prompt": "My email is secret@corp.com",
        "response": "Noted",
        "model": "gpt-4",
    }
    await exporter.record(entry)
    await exporter.close()

    files = list(tmp_path.iterdir())
    assert len(files) >= 1
    content = files[0].read_text()
    assert "secret@corp.com" not in content
