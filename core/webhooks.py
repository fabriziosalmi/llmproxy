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

import hmac
import hashlib
import ipaddress
import json
import socket
import time
import logging
import asyncio
import aiohttp
import urllib.parse
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


# SSRF — private/reserved CIDR ranges that webhook URLs must not target
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),      # loopback
    ipaddress.ip_network("10.0.0.0/8"),        # RFC-1918
    ipaddress.ip_network("172.16.0.0/12"),     # RFC-1918
    ipaddress.ip_network("192.168.0.0/16"),    # RFC-1918
    ipaddress.ip_network("169.254.0.0/16"),    # link-local / AWS IMDS
    ipaddress.ip_network("::1/128"),           # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),          # IPv6 ULA
    ipaddress.ip_network("fe80::/10"),         # IPv6 link-local
]


def _validate_webhook_url(url: str) -> None:
    """Raise ValueError if url is structurally invalid or targets a private IP literal.

    This is a *load-time* structural check only — it catches obvious mistakes
    (wrong scheme, bare IP targeting internal ranges) but does NOT prevent DNS
    rebinding for hostname-based URLs.  The actual runtime SSRF guard is
    _SSRFBlockingResolver, which validates the resolved IP at aiohttp connect
    time, after every DNS lookup, preventing rebinding attacks.
    """
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception as exc:
        raise ValueError(f"Malformed webhook URL: {exc}") from exc

    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Webhook URL scheme '{parsed.scheme}' is not allowed (must be http/https)")

    host = parsed.hostname or ""
    if not host:
        raise ValueError("Webhook URL has no host")

    # Only check IP literals here — no DNS resolution (TOCTOU / DNS rebinding risk).
    # Hostname-based URLs are validated by _SSRFBlockingResolver at connect time.
    try:
        ip = ipaddress.ip_address(host)
        for net in _PRIVATE_NETWORKS:
            if ip in net:
                raise ValueError(f"Webhook URL targets private/reserved IP {ip}")
    except ValueError as exc:
        if "private/reserved" in str(exc):
            raise
        # Not an IP literal — hostname validation deferred to _SSRFBlockingResolver


class _SSRFBlockingResolver(aiohttp.abc.AbstractResolver):
    """aiohttp resolver that validates resolved IPs against private CIDR ranges.

    By plugging into the TCPConnector, this check runs at the moment aiohttp
    resolves a hostname before opening a TCP socket — AFTER every DNS lookup.
    This prevents DNS rebinding: an attacker cannot serve a public IP during
    _validate_webhook_url() at load time and a private IP at request time,
    because we re-check every time a connection is established.

    Fail-closed: DNS failures propagate as OSError → aiohttp raises
    ClientConnectorError, the webhook delivery fails, and the exception is
    logged.  We never silently allow an unresolved hostname through.
    """

    def __init__(self) -> None:
        self._resolver = aiohttp.resolver.DefaultResolver()

    async def resolve(
        self, hostname: str, port: int = 0, family: int = socket.AF_INET
    ) -> list:
        # DNS failure propagates here — fail-closed by default (no except)
        addrs = await self._resolver.resolve(hostname, port, family)
        for addr in addrs:
            try:
                ip = ipaddress.ip_address(addr["host"])
            except ValueError:
                continue
            for net in _PRIVATE_NETWORKS:
                if ip in net:
                    raise OSError(
                        f"SSRF blocked: {hostname!r} resolved to "
                        f"private/reserved IP {ip} ({net})"
                    )
        return addrs

    async def close(self) -> None:
        await self._resolver.close()


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

            try:
                _validate_webhook_url(url)
            except ValueError as exc:
                logger.error(f"Webhook '{name}': SSRF guard rejected URL — {exc}. Skipping.")
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
            # _SSRFBlockingResolver validates the resolved IP at connect time,
            # preventing DNS rebinding attacks that bypass load-time URL checks.
            connector = aiohttp.TCPConnector(resolver=_SSRFBlockingResolver())
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=aiohttp.ClientTimeout(total=10),
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
            # Serialize to bytes once so both the HMAC and the POST body are identical
            payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers = {"Content-Type": "application/json"}
            # HMAC-SHA256 payload signing — authenticates the payload to the receiver
            if endpoint.secret:
                sig = hmac.new(
                    endpoint.secret.encode("utf-8"), payload_bytes, hashlib.sha256
                ).hexdigest()
                headers["X-Webhook-Signature"] = f"sha256={sig}"

            async with session.post(endpoint.url, data=payload_bytes, headers=headers) as resp:
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
