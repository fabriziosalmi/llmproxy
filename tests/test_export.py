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
    exporter = DatasetExporter(output_dir=str(tmp_path), scrub=False, compress_on_rotate=False)
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
    exporter = DatasetExporter(output_dir=str(tmp_path), scrub=True, compress_on_rotate=False)
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
