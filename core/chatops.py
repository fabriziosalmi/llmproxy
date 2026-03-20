"""
LLMPROXY — ChatOps Module (Session 8.3-8.5)

Telegram bot for operational commands + HITL (Human-in-the-Loop) approval.
Long-polling asyncio loop, no webhook server required.

Commands:
  /status   — Show proxy status, pool health, budget
  /kill     — Kill a specific node (requires confirmation)
  /approve  — Approve a pending HITL request
  /reject   — Reject a pending HITL request
  /budget   — Show budget consumption

HITL Flow:
  1. SecurityShield flags a "soft-violation" request
  2. Request enters hold queue with timeout
  3. Telegram notification sent to ops channel
  4. Operator replies /approve <id> or /reject <id>
  5. Request continues or is rejected

Auto-Ticketing:
  - >50 errors/hr → creates a summary alert (debounced)
"""

import json
import time
import logging
import asyncio
import aiohttp
from typing import Dict, Any, Optional, List, Callable, Awaitable
from dataclasses import dataclass, field
from datetime import datetime

from core.infisical import get_secret

logger = logging.getLogger(__name__)


@dataclass
class HITLRequest:
    """A pending human-in-the-loop approval request."""
    id: str
    summary: str
    payload: Dict[str, Any]
    created_at: float = field(default_factory=time.time)
    timeout: float = 300  # 5 minutes
    resolved: bool = False
    approved: Optional[bool] = None
    _event: asyncio.Event = field(default_factory=asyncio.Event)

    def approve(self):
        self.approved = True
        self.resolved = True
        self._event.set()

    def reject(self):
        self.approved = False
        self.resolved = True
        self._event.set()

    async def wait(self) -> bool:
        """Wait for approval. Returns True if approved, False if rejected/timeout."""
        try:
            await asyncio.wait_for(self._event.wait(), timeout=self.timeout)
            return self.approved is True
        except asyncio.TimeoutError:
            self.resolved = True
            self.approved = False
            return False


class TelegramBot:
    """
    Async Telegram bot via long-polling.
    No webhook server needed — uses getUpdates API.
    """

    def __init__(self, config: Dict[str, Any]):
        chatops_cfg = config.get("chatops", {}).get("telegram", {})
        self.enabled = chatops_cfg.get("enabled", False)

        token_env = chatops_cfg.get("token_env", "TELEGRAM_BOT_TOKEN")
        self.token = get_secret(token_env, required=False) if self.enabled else None
        self.chat_id = chatops_cfg.get("chat_id")  # Target chat/group

        self._session: Optional[aiohttp.ClientSession] = None
        self._offset = 0
        self._running = False
        self._handlers: Dict[str, Callable[..., Awaitable]] = {}

        # HITL queue
        self.hitl_queue: Dict[str, HITLRequest] = {}

        # Auto-ticketing state
        self._error_count = 0
        self._error_window_start = time.time()
        self._last_ticket_time = 0

        if self.enabled and self.token:
            self._register_default_handlers()

    @property
    def api_url(self) -> str:
        return f"https://api.telegram.org/bot{self.token}"

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=35)
            )
        return self._session

    def _register_default_handlers(self):
        """Register built-in command handlers."""

        async def cmd_status(chat_id: int, args: str):
            await self.send_message(chat_id,
                "📊 *LLMPROXY Status*\n"
                "• Proxy: Active\n"
                "• Use `/budget` for cost info\n"
                "• Use `/approve <id>` to approve pending requests",
                parse_mode="Markdown"
            )

        async def cmd_approve(chat_id: int, args: str):
            req_id = args.strip()
            if not req_id:
                await self.send_message(chat_id, "Usage: /approve <request_id>")
                return
            req = self.hitl_queue.get(req_id)
            if not req or req.resolved:
                await self.send_message(chat_id, f"❌ Request `{req_id}` not found or already resolved")
                return
            req.approve()
            await self.send_message(chat_id, f"✅ Request `{req_id}` approved")

        async def cmd_reject(chat_id: int, args: str):
            req_id = args.strip()
            if not req_id:
                await self.send_message(chat_id, "Usage: /reject <request_id>")
                return
            req = self.hitl_queue.get(req_id)
            if not req or req.resolved:
                await self.send_message(chat_id, f"❌ Request `{req_id}` not found or already resolved")
                return
            req.reject()
            await self.send_message(chat_id, f"🚫 Request `{req_id}` rejected")

        async def cmd_budget(chat_id: int, args: str):
            await self.send_message(chat_id,
                "💰 *Budget*\n• Check Prometheus /metrics for real-time data",
                parse_mode="Markdown"
            )

        self._handlers = {
            "/status": cmd_status,
            "/approve": cmd_approve,
            "/reject": cmd_reject,
            "/budget": cmd_budget,
        }

    async def send_message(self, chat_id: int, text: str, parse_mode: str = "Markdown"):
        """Send a message to a Telegram chat."""
        if not self.token:
            return
        try:
            session = await self._get_session()
            await session.post(f"{self.api_url}/sendMessage", json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
            })
        except Exception as e:
            logger.error(f"Telegram: Failed to send message: {e}")

    async def notify_ops(self, text: str):
        """Send a notification to the configured ops chat."""
        if self.chat_id:
            await self.send_message(int(self.chat_id), text)

    async def request_approval(self, request_id: str, summary: str, payload: Dict[str, Any]) -> bool:
        """
        8.4: HITL — Submit a request for human approval.
        Returns True if approved, False if rejected or timed out.
        """
        req = HITLRequest(id=request_id, summary=summary, payload=payload)
        self.hitl_queue[request_id] = req

        await self.notify_ops(
            f"⚠️ *Approval Required*\n"
            f"ID: `{request_id}`\n"
            f"Summary: {summary}\n\n"
            f"Reply `/approve {request_id}` or `/reject {request_id}`"
        )

        result = await req.wait()
        # Cleanup
        self.hitl_queue.pop(request_id, None)
        return result

    async def track_error(self):
        """
        8.5: Debounced auto-ticketing.
        Tracks errors and sends summary alert if >50 errors/hr.
        """
        now = time.time()
        # Reset window every hour
        if now - self._error_window_start > 3600:
            self._error_count = 0
            self._error_window_start = now

        self._error_count += 1

        if self._error_count >= 50 and (now - self._last_ticket_time > 600):
            self._last_ticket_time = now
            await self.notify_ops(
                f"🚨 *Auto-Ticket: High Error Rate*\n"
                f"• {self._error_count} errors in the last hour\n"
                f"• Timestamp: {datetime.utcnow().isoformat()}Z\n"
                f"• Action: Check `/status` and Prometheus /metrics"
            )
            logger.warning(f"ChatOps: Auto-ticket triggered — {self._error_count} errors/hr")

    async def start_polling(self):
        """Start the long-polling loop for Telegram updates."""
        if not self.enabled or not self.token:
            return
        self._running = True
        logger.info("Telegram bot: Starting long-poll loop")

        while self._running:
            try:
                session = await self._get_session()
                async with session.get(
                    f"{self.api_url}/getUpdates",
                    params={"offset": self._offset, "timeout": 30}
                ) as resp:
                    if resp.status != 200:
                        await asyncio.sleep(5)
                        continue
                    data = await resp.json()
                    for update in data.get("result", []):
                        self._offset = update["update_id"] + 1
                        await self._handle_update(update)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Telegram poll error: {e}")
                await asyncio.sleep(5)

    async def _handle_update(self, update: Dict[str, Any]):
        """Process a single Telegram update."""
        message = update.get("message", {})
        text = message.get("text", "")
        chat_id = message.get("chat", {}).get("id")
        if not text or not chat_id:
            return

        # Parse command
        parts = text.strip().split(maxsplit=1)
        command = parts[0].lower().split("@")[0]  # Strip @botname
        args = parts[1] if len(parts) > 1 else ""

        handler = self._handlers.get(command)
        if handler:
            try:
                await handler(chat_id, args)
            except Exception as e:
                logger.error(f"Telegram handler error for '{command}': {e}")
                await self.send_message(chat_id, f"❌ Error: {e}")

    async def stop(self):
        self._running = False
        if self._session and not self._session.closed:
            await self._session.close()
