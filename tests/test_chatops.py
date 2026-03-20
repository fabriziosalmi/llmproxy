"""Tests for core.chatops.TelegramBot and HITLRequest."""

import pytest
import asyncio
from unittest.mock import patch, AsyncMock
from core.chatops import TelegramBot, HITLRequest


DISABLED_CONFIG = {"chatops": {"telegram": {"enabled": False}}}


@pytest.fixture
def bot():
    with patch("core.chatops.get_secret", return_value=None):
        return TelegramBot(DISABLED_CONFIG)


@pytest.mark.asyncio
async def test_disabled_no_polling(bot):
    # When disabled, start_polling should return immediately without error
    await bot.start_polling()


@pytest.mark.asyncio
async def test_hitl_approve():
    req = HITLRequest(
        id="req-001",
        summary="Deploy model v2?",
        payload={"model": "v2"},
        timeout=5.0,
    )
    req.approve()
    result = await req.wait()
    assert result is True


@pytest.mark.asyncio
async def test_hitl_reject():
    req = HITLRequest(
        id="req-002",
        summary="Delete all logs?",
        payload={"action": "delete_logs"},
        timeout=5.0,
    )
    req.reject()
    result = await req.wait()
    assert result is False


@pytest.mark.asyncio
async def test_hitl_timeout():
    req = HITLRequest(
        id="req-003",
        summary="Timeout test",
        payload={},
        timeout=0.1,
    )
    result = await req.wait()
    assert result is False


@pytest.mark.asyncio
async def test_error_tracking(bot):
    # track_error() is async and takes no args
    for i in range(51):
        await bot.track_error()
    assert bot._error_count >= 50
