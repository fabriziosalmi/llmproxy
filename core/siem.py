"""
SIEM export — turn LLMProxy security events into the two formats every SOC
already ingests:

  - CEF (ArcSight Common Event Format) — the syslog lingua franca for QRadar,
    ArcSight, and most on-prem SIEMs.
  - ECS (Elastic Common Schema) JSON — for Elastic, Splunk (HEC), Datadog,
    and modern log pipelines.

Both are PURE functions (no I/O), so they are trivially unit-testable and the
delivery mechanism (webhook HTTP POST, syslog) is a separate concern. Field
escaping follows each spec exactly — a malformed event must not be able to
inject a second event or break the parser downstream.
"""
from __future__ import annotations

from typing import Any, Dict

_VENDOR = "llmproxy"
_PRODUCT = "llmproxy"

# CEF severity is 0-10. Map each event type to a defensible level.
_CEF_SEVERITY: Dict[str, int] = {
    "panic_activated": 10,
    "injection_blocked": 8,
    "auth_failure": 6,
    "budget_threshold": 5,
    "circuit_open": 4,
    "endpoint_down": 4,
    "endpoint_recovered": 2,
}

# ECS event.category / event.type per event (https://www.elastic.co/guide/en/ecs).
_ECS_META: Dict[str, Dict[str, Any]] = {
    "injection_blocked": {"category": ["intrusion_detection"], "type": ["denied"], "kind": "alert"},
    "auth_failure": {"category": ["authentication"], "type": ["denied"], "kind": "alert"},
    "panic_activated": {"category": ["configuration"], "type": ["change"], "kind": "alert"},
    "budget_threshold": {"category": ["configuration"], "type": ["info"], "kind": "event"},
    "circuit_open": {"category": ["network"], "type": ["error"], "kind": "event"},
    "endpoint_down": {"category": ["network"], "type": ["error"], "kind": "event"},
    "endpoint_recovered": {"category": ["network"], "type": ["info"], "kind": "event"},
}

# Human-readable CEF "Name" per event.
_CEF_NAME: Dict[str, str] = {
    "injection_blocked": "Prompt injection blocked",
    "auth_failure": "Authentication failure",
    "panic_activated": "Kill-switch activated",
    "budget_threshold": "Budget threshold reached",
    "circuit_open": "Upstream circuit opened",
    "endpoint_down": "Endpoint down",
    "endpoint_recovered": "Endpoint recovered",
}


def _cef_escape_header(value: str) -> str:
    """Header fields escape backslash and pipe only."""
    return value.replace("\\", "\\\\").replace("|", "\\|")


def _cef_escape_ext(value: str) -> str:
    """Extension values escape backslash, equals, and newlines."""
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("=", "\\=")
        .replace("\n", "\\n")
        .replace("\r", "\\n")
    )


# Map common LLMProxy data keys → CEF dictionary keys (falls back to cs* labels).
_CEF_KEY_MAP = {
    "ip": "src",
    "source_ip": "src",
    "user": "suser",
    "key_prefix": "suser",
    "reason": "reason",
    "endpoint": "deviceCustomString1",
    "model": "deviceCustomString2",
    "message": "msg",
}


def to_cef(
    event_type: str,
    data: Dict[str, Any] | None = None,
    *,
    version: str = "unknown",
) -> str:
    """Format a security event as an ArcSight CEF:0 line.

    CEF:0|Vendor|Product|Version|SignatureID|Name|Severity|Extension
    """
    data = data or {}
    name = _CEF_NAME.get(event_type, event_type.replace("_", " ").title())
    severity = _CEF_SEVERITY.get(event_type, 3)
    header = "|".join(
        [
            "CEF:0",
            _cef_escape_header(_VENDOR),
            _cef_escape_header(_PRODUCT),
            _cef_escape_header(version),
            _cef_escape_header(event_type),
            _cef_escape_header(name),
            str(severity),
        ]
    )
    parts = []
    for k, v in data.items():
        if v is None:
            continue
        cef_key = _CEF_KEY_MAP.get(k, k)
        parts.append(f"{cef_key}={_cef_escape_ext(v)}")
    extension = " ".join(parts)
    return f"{header}|{extension}" if extension else header


def to_ecs(
    event_type: str,
    data: Dict[str, Any] | None = None,
    *,
    timestamp: str,
    version: str = "unknown",
) -> Dict[str, Any]:
    """Format a security event as an Elastic Common Schema (ECS) document.

    `timestamp` is passed in (RFC3339) so this stays a pure function.
    """
    data = data or {}
    meta = _ECS_META.get(event_type, {"category": ["configuration"], "type": ["info"], "kind": "event"})
    doc: Dict[str, Any] = {
        "@timestamp": timestamp,
        "event": {
            "kind": meta["kind"],
            "category": meta["category"],
            "type": meta["type"],
            "action": event_type,
            "severity": _CEF_SEVERITY.get(event_type, 3),
            "provider": _PRODUCT,
        },
        "observer": {"vendor": _VENDOR, "product": _PRODUCT, "type": "proxy", "version": version},
        "message": str(data.get("message") or _CEF_NAME.get(event_type, event_type)),
    }
    src_ip = data.get("ip") or data.get("source_ip")
    if src_ip:
        doc["source"] = {"ip": str(src_ip)}
    user = data.get("user") or data.get("key_prefix")
    if user:
        doc["user"] = {"name": str(user)}
    # Preserve the raw event fields under a namespaced key for full fidelity.
    doc["llmproxy"] = {k: v for k, v in data.items() if v is not None}
    return doc
