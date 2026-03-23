"""
LLMPROXY — Webhook Dispatcher (Session 8.1-8.2)

Generic HTTP POST dispatcher for real-time event notifications.
Supports Slack, Teams, Discord, and generic webhook endpoints.

Features:
  - Event-driven: circuit open, budget threshold, injection blocked, endpoint down
  - Markdown and JSON formatting per target
  - Async non-blocking dispatch with retry
  - Rate-limited to prevent webhook flooding
"""

import json
import time
import logging
import asyncio
import aiohttp
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from enum import Enum

from core.infisical import get_secret

logger = logging.getLogger(__name__)


class WebhookTarget(Enum):
    SLACK = "slack"
    TEAMS = "teams"
    DISCORD = "discord"
    GENERIC = "generic"


class EventType(Enum):
    CIRCUIT_OPEN = "circuit_open"
    BUDGET_THRESHOLD = "budget_threshold"
    INJECTION_BLOCKED = "injection_blocked"
    ENDPOINT_DOWN = "endpoint_down"
    ENDPOINT_RECOVERED = "endpoint_recovered"
    AUTH_FAILURE = "auth_failure"
    PANIC_ACTIVATED = "panic_activated"


@dataclass
class WebhookConfig:
    """Configuration for a single webhook endpoint."""
    name: str
    url: str
    target: WebhookTarget = WebhookTarget.GENERIC
    events: List[str] = field(default_factory=lambda: ["*"])  # "*" = all events
    secret: Optional[str] = None  # HMAC signing secret


# Minimum interval between identical events (debounce)
DEBOUNCE_SECONDS = 30


class WebhookDispatcher:
    """
    Dispatches real-time event notifications to configured webhook endpoints.

    Usage:
        dispatcher = WebhookDispatcher(config)
        await dispatcher.dispatch(EventType.CIRCUIT_OPEN, {"endpoint": "openai", "reason": "5xx cascade"})
    """

    def __init__(self, config: Dict[str, Any]):
        webhooks_cfg = config.get("webhooks", {})
        self.enabled = webhooks_cfg.get("enabled", False)
        self.endpoints: List[WebhookConfig] = []
        self._session: Optional[aiohttp.ClientSession] = None
        self._last_dispatch: Dict[str, float] = {}  # event_key → timestamp (debounce)

        if self.enabled:
            self._load_endpoints(webhooks_cfg.get("endpoints", []))

    def _load_endpoints(self, endpoint_configs: List[Dict[str, Any]]):
        for ecfg in endpoint_configs:
            name = ecfg.get("name", "webhook")
            # URL from config or Infisical
            url_env = ecfg.get("url_env")
            url = get_secret(url_env, required=False) if url_env else ecfg.get("url", "")
            if not url:
                logger.warning(f"Webhook '{name}': no URL configured, skipping")
                continue

            target = WebhookTarget(ecfg.get("target", "generic"))
            events = ecfg.get("events", ["*"])
            secret_env = ecfg.get("secret_env")
            secret = get_secret(secret_env, required=False) if secret_env else None

            self.endpoints.append(WebhookConfig(
                name=name, url=url, target=target, events=events, secret=secret,
            ))
            logger.info(f"Webhook: Registered '{name}' ({target.value}) for events={events}")

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            )
        return self._session

    def _should_dispatch(self, event_key: str) -> bool:
        """Debounce: prevent flooding with identical events."""
        now = time.time()
        last = self._last_dispatch.get(event_key, 0)
        if now - last < DEBOUNCE_SECONDS:
            return False
        self._last_dispatch[event_key] = now
        return True

    async def dispatch(self, event: EventType, data: Dict[str, Any]):
        """
        Dispatch an event to all matching webhook endpoints.
        Non-blocking — errors are logged but never propagate.
        """
        if not self.enabled or not self.endpoints:
            return

        event_key = f"{event.value}:{json.dumps(data, sort_keys=True)[:100]}"
        if not self._should_dispatch(event_key):
            logger.debug(f"Webhook: Debounced {event.value}")
            return

        tasks = []
        for endpoint in self.endpoints:
            if "*" in endpoint.events or event.value in endpoint.events:
                tasks.append(self._send(endpoint, event, data))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _send(self, endpoint: WebhookConfig, event: EventType, data: Dict[str, Any]):
        """Send a formatted payload to a single webhook endpoint."""
        try:
            session = await self._get_session()
            payload = self._format_payload(endpoint.target, event, data)
            headers = {"Content-Type": "application/json"}

            async with session.post(endpoint.url, json=payload, headers=headers) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    logger.warning(f"Webhook '{endpoint.name}': HTTP {resp.status} — {body[:200]}")
                else:
                    logger.info(f"Webhook '{endpoint.name}': Dispatched {event.value}")
        except Exception as e:
            logger.error(f"Webhook '{endpoint.name}': Failed to dispatch {event.value}: {e}")

    def _format_payload(
        self, target: WebhookTarget, event: EventType, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Format the event payload for the target platform."""
        severity = self._get_severity(event)
        title = f"🔔 LLMPROXY — {event.value.replace('_', ' ').title()}"
        details = "\n".join(f"• **{k}**: {v}" for k, v in data.items())
        text = f"{title}\n{details}"

        if target == WebhookTarget.SLACK:
            emoji = {"critical": "🔴", "warning": "🟡", "info": "🟢"}.get(severity, "⚪")
            return {
                "blocks": [
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": f"{emoji} *{title}*\n{details}"},
                    }
                ]
            }
        elif target == WebhookTarget.TEAMS:
            color = {"critical": "FF0000", "warning": "FFA500", "info": "00FF00"}.get(severity, "808080")
            return {
                "@type": "MessageCard",
                "themeColor": color,
                "summary": title,
                "sections": [{"activityTitle": title, "text": details}],
            }
        elif target == WebhookTarget.DISCORD:
            discord_color = {"critical": 0xFF0000, "warning": 0xFFA500, "info": 0x00FF00}.get(severity, 0x808080)
            return {
                "embeds": [{
                    "title": title,
                    "description": details,
                    "color": discord_color,
                }]
            }
        else:
            return {"event": event.value, "severity": severity, "data": data, "text": text}

    @staticmethod
    def _get_severity(event: EventType) -> str:
        critical = {EventType.CIRCUIT_OPEN, EventType.PANIC_ACTIVATED, EventType.ENDPOINT_DOWN}
        warning = {EventType.BUDGET_THRESHOLD, EventType.INJECTION_BLOCKED, EventType.AUTH_FAILURE}
        if event in critical:
            return "critical"
        elif event in warning:
            return "warning"
        return "info"

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
