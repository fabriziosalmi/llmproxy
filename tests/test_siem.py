"""SIEM export formatters. The escaping is security-critical: a crafted event
field must not be able to forge a second CEF field or break the parser."""
import json

from core.siem import to_cef, to_ecs


# ── CEF ──────────────────────────────────────────────────────────────────────
def test_cef_header_shape():
    line = to_cef("auth_failure", {"ip": "1.2.3.4"}, version="1.24.1")
    assert line.startswith("CEF:0|llmproxy|llmproxy|1.24.1|auth_failure|Authentication failure|6|")
    assert "src=1.2.3.4" in line


def test_cef_maps_known_keys_to_dictionary():
    line = to_cef("injection_blocked", {"ip": "10.0.0.1", "key_prefix": "sk-abc", "model": "gpt-4o"})
    assert "src=10.0.0.1" in line
    assert "suser=sk-abc" in line
    assert "deviceCustomString2=gpt-4o" in line


def test_cef_severity_per_event():
    # Severity is the 7th (0-indexed 6th) pipe-delimited header field.
    def sev(evt):
        return to_cef(evt, {}).split("|")[6]

    assert sev("panic_activated") == "10"
    assert sev("injection_blocked") == "8"
    assert sev("some_unknown_event") == "3"  # default


def test_cef_escapes_pipe_and_backslash_in_header():
    # version is a header field → pipe/backslash must be escaped, not split it.
    line = to_cef("auth_failure", {}, version="a|b\\c")
    # The escaped version appears; the raw pipe does not create an 8th header field.
    assert "a\\|b\\\\c" in line


def test_cef_escapes_equals_and_newline_in_extension():
    # A value containing '=' or newline must not forge a second key=value pair.
    line = to_cef("injection_blocked", {"reason": "x=1\ninjected=evil"})
    assert "reason=x\\=1\\ninjected\\=evil" in line
    # There is exactly one real extension separator for this single field.
    ext = line.split("|", 7)[7]
    assert ext.count(" ") == 0  # single field, no spurious space-separated pairs


def test_cef_skips_none_values():
    line = to_cef("auth_failure", {"ip": "1.1.1.1", "user": None})
    assert "src=1.1.1.1" in line
    assert "suser=" not in line


# ── ECS ──────────────────────────────────────────────────────────────────────
def test_ecs_shape_and_categories():
    doc = to_ecs("injection_blocked", {"ip": "9.9.9.9", "message": "blocked it"}, timestamp="2026-07-01T00:00:00Z")
    assert doc["@timestamp"] == "2026-07-01T00:00:00Z"
    assert doc["event"]["action"] == "injection_blocked"
    assert doc["event"]["category"] == ["intrusion_detection"]
    assert doc["event"]["kind"] == "alert"
    assert doc["source"]["ip"] == "9.9.9.9"
    assert doc["observer"]["vendor"] == "llmproxy"
    assert doc["message"] == "blocked it"


def test_ecs_is_json_serialisable_and_preserves_raw_fields():
    doc = to_ecs("auth_failure", {"ip": "2.2.2.2", "key_prefix": "sk-x", "reason": "bad key"}, timestamp="t")
    s = json.dumps(doc)  # must not raise
    assert json.loads(s)["llmproxy"]["reason"] == "bad key"
    assert doc["user"]["name"] == "sk-x"


def test_ecs_unknown_event_defaults():
    doc = to_ecs("mystery", {}, timestamp="t")
    assert doc["event"]["kind"] == "event"
    assert doc["event"]["action"] == "mystery"
