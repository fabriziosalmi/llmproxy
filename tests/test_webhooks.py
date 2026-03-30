"""Tests for core.webhooks.WebhookDispatcher."""

import pytest
from core.webhooks import WebhookDispatcher, EventType, WebhookTarget


DISABLED_CONFIG = {"webhooks": {"enabled": False}}


@pytest.fixture
def disabled_dispatcher():
    return WebhookDispatcher(DISABLED_CONFIG)


@pytest.mark.asyncio
async def test_disabled_does_nothing(disabled_dispatcher):
    # Should return immediately without making any HTTP calls
    await disabled_dispatcher.dispatch(EventType.CIRCUIT_OPEN, {"endpoint": "ep1"})


def test_format_slack(disabled_dispatcher):
    payload = disabled_dispatcher._format_payload(
        WebhookTarget.SLACK,
        EventType.CIRCUIT_OPEN,
        {"endpoint": "ep1", "reason": "timeout"},
    )
    assert isinstance(payload, dict)
    assert "blocks" in payload


def test_format_teams(disabled_dispatcher):
    payload = disabled_dispatcher._format_payload(
        WebhookTarget.TEAMS,
        EventType.AUTH_FAILURE,
        {"user": "attacker@evil.com"},
    )
    assert isinstance(payload, dict)
    assert payload.get("@type") == "MessageCard"


def test_format_discord(disabled_dispatcher):
    payload = disabled_dispatcher._format_payload(
        WebhookTarget.DISCORD,
        EventType.ENDPOINT_RECOVERED,
        {"endpoint": "ep1"},
    )
    assert isinstance(payload, dict)
    assert "embeds" in payload


def test_severity_mapping():
    assert WebhookDispatcher._get_severity(EventType.CIRCUIT_OPEN) == "critical"
    assert WebhookDispatcher._get_severity(EventType.AUTH_FAILURE) == "warning"
    assert WebhookDispatcher._get_severity(EventType.ENDPOINT_RECOVERED) == "info"


def test_format_generic(disabled_dispatcher):
    payload = disabled_dispatcher._format_payload(
        WebhookTarget.GENERIC,
        EventType.PANIC_ACTIVATED,
        {"action": "kill"},
    )
    assert payload["event"] == "panic_activated"
    assert payload["severity"] == "critical"
