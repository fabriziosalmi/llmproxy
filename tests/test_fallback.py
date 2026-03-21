"""
Tests for cross-provider fallback logic in RotatorAgent._forward_with_fallback.

Validates that when the primary provider fails (circuit open, HTTP error,
connection error), the proxy automatically tries fallback providers from
the configured fallback_chains.
"""

import asyncio
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException
from starlette.responses import Response

from core.circuit_breaker import CircuitManager


# ── Minimal RotatorAgent stub for fallback testing ──

class FallbackTestAgent:
    """Minimal agent with just enough state to test _forward_with_fallback."""

    def __init__(self, config=None):
        self.config = config or {}
        self.circuit_manager = CircuitManager()
        self.logger = MagicMock()
        self.log_queue = asyncio.Queue(maxsize=100)

        from proxy.rotator import RotatorAgent
        import types
        self._forward_with_fallback = types.MethodType(
            RotatorAgent._forward_with_fallback, self,
        )
        self._forward_request = types.MethodType(
            RotatorAgent._forward_request, self,
        )
        self._resolve_endpoint_for_provider = types.MethodType(
            RotatorAgent._resolve_endpoint_for_provider, self,
        )
        self._add_log = types.MethodType(RotatorAgent._add_log, self)


def _make_target(provider="openai", url="https://api.openai.com/v1"):
    return SimpleNamespace(id=f"{provider}-ep", url=url, provider=provider, provider_type=provider)


def _make_ctx(model="gpt-4o", stream=False):
    return SimpleNamespace(
        body={"model": model, "messages": [{"role": "user", "content": "Hi"}], "stream": stream},
        metadata={},
        response=None,
        session_id="test",
    )


def _ok_response(status=200):
    return Response(content=b'{"choices":[{"message":{"content":"ok"}}]}', status_code=status)


def _error_response(status=503):
    return Response(content=b'{"error":"upstream down"}', status_code=status)


def _make_mock_adapter(name="openai", response=None, error=None):
    """Create a mock adapter that returns a pre-built Response or raises."""
    adapter = MagicMock()
    adapter.provider_name = name
    adapter.translate_request.return_value = ("http://mock-url", {"model": "test"}, {})
    if error:
        adapter.request = AsyncMock(side_effect=error)
    elif response:
        adapter.request = AsyncMock(return_value=response)
    else:
        adapter.request = AsyncMock(return_value=_ok_response())
    return adapter


# ══════════════════════════════════════════════════════
# Basic Fallback
# ══════════════════════════════════════════════════════

class TestFallbackBasic:

    @pytest.mark.asyncio
    async def test_primary_success_no_fallback(self):
        """When primary succeeds, no fallback is attempted."""
        agent = FallbackTestAgent(config={
            "endpoints": {"openai": {"provider": "openai", "base_url": "https://api.openai.com/v1"}},
            "fallback_chains": {"gpt-4o": [{"provider": "anthropic", "model": "claude-sonnet-4-20250514"}]},
        })
        ctx = _make_ctx("gpt-4o")
        target = _make_target("openai")
        mock_adapter = _make_mock_adapter("openai", _ok_response())

        with patch("proxy.rotator.get_adapter", return_value=mock_adapter):
            await agent._forward_with_fallback(ctx, target, {}, MagicMock())

        assert ctx.response.status_code == 200
        assert "_fallback_used" not in ctx.metadata

    @pytest.mark.asyncio
    async def test_primary_fails_fallback_succeeds(self):
        """When primary returns 503, fallback provider is used."""
        agent = FallbackTestAgent(config={
            "endpoints": {
                "openai": {"provider": "openai", "base_url": "https://api.openai.com/v1"},
                "anthropic": {"provider": "anthropic", "base_url": "https://api.anthropic.com/v1"},
            },
            "fallback_chains": {"gpt-4o": [{"provider": "anthropic", "model": "claude-sonnet-4-20250514"}]},
        })
        ctx = _make_ctx("gpt-4o")
        target = _make_target("openai")

        primary = _make_mock_adapter("openai", _error_response(503))
        fallback = _make_mock_adapter("anthropic", _ok_response())
        call_count = 0

        def get_adapter_side_effect(provider_type=None, model=""):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return primary
            return fallback

        with patch("proxy.rotator.get_adapter", side_effect=get_adapter_side_effect):
            await agent._forward_with_fallback(ctx, target, {}, MagicMock())

        assert ctx.response.status_code == 200
        assert ctx.metadata.get("_fallback_used") == "anthropic"
        assert ctx.metadata.get("_fallback_model") == "claude-sonnet-4-20250514"
        assert ctx.metadata.get("_original_model") == "gpt-4o"

    @pytest.mark.asyncio
    async def test_all_providers_fail_raises(self):
        """When all providers fail, raise the last error."""
        agent = FallbackTestAgent(config={
            "endpoints": {
                "openai": {"provider": "openai", "base_url": "https://api.openai.com/v1"},
                "anthropic": {"provider": "anthropic", "base_url": "https://api.anthropic.com/v1"},
            },
            "fallback_chains": {"gpt-4o": [{"provider": "anthropic", "model": "claude-sonnet-4-20250514"}]},
        })
        ctx = _make_ctx("gpt-4o")
        target = _make_target("openai")
        mock = _make_mock_adapter("openai", _error_response(503))

        with patch("proxy.rotator.get_adapter", return_value=mock):
            with pytest.raises(HTTPException) as exc_info:
                await agent._forward_with_fallback(ctx, target, {}, MagicMock())
            assert exc_info.value.status_code == 503

    @pytest.mark.asyncio
    async def test_no_fallback_chain_raises_directly(self):
        """Without a fallback chain, primary failure raises immediately."""
        agent = FallbackTestAgent(config={
            "endpoints": {"openai": {"provider": "openai", "base_url": "https://api.openai.com/v1"}},
        })
        ctx = _make_ctx("gpt-4o")
        target = _make_target("openai")
        mock = _make_mock_adapter("openai", _error_response(500))

        with patch("proxy.rotator.get_adapter", return_value=mock):
            with pytest.raises(HTTPException) as exc_info:
                await agent._forward_with_fallback(ctx, target, {}, MagicMock())
            assert exc_info.value.status_code == 500


# ══════════════════════════════════════════════════════
# Circuit Breaker Integration
# ══════════════════════════════════════════════════════

class TestFallbackCircuitBreaker:

    @pytest.mark.asyncio
    async def test_circuit_open_triggers_fallback(self):
        """When primary circuit is open, skip to fallback."""
        agent = FallbackTestAgent(config={
            "endpoints": {
                "openai": {"provider": "openai", "base_url": "https://api.openai.com/v1"},
                "anthropic": {"provider": "anthropic", "base_url": "https://api.anthropic.com/v1"},
            },
            "fallback_chains": {"gpt-4o": [{"provider": "anthropic", "model": "claude-sonnet-4-20250514"}]},
        })
        ctx = _make_ctx("gpt-4o")
        target = _make_target("openai")

        # Open the primary circuit breaker
        cb = agent.circuit_manager.get_breaker("openai-ep")
        for _ in range(10):
            cb.report_failure()
        assert not cb.can_execute()

        primary = _make_mock_adapter("openai")
        fallback = _make_mock_adapter("anthropic", _ok_response())
        call_count = 0

        def get_adapter_side_effect(provider_type=None, model=""):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return primary
            return fallback

        with patch("proxy.rotator.get_adapter", side_effect=get_adapter_side_effect):
            await agent._forward_with_fallback(ctx, target, {}, MagicMock())

        assert ctx.response.status_code == 200
        assert ctx.metadata.get("_fallback_used") == "anthropic"
        # Primary adapter.request should NOT have been called
        primary.request.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_all_circuits_open_raises_503(self):
        """When all circuits are open, raise 503."""
        agent = FallbackTestAgent(config={
            "endpoints": {
                "openai": {"provider": "openai", "base_url": "https://api.openai.com/v1"},
                "anthropic": {"provider": "anthropic", "base_url": "https://api.anthropic.com/v1"},
            },
            "fallback_chains": {"gpt-4o": [{"provider": "anthropic", "model": "claude-sonnet-4-20250514"}]},
        })
        ctx = _make_ctx("gpt-4o")
        target = _make_target("openai")

        # Open both circuits
        for ep_id in ["openai-ep", "anthropic"]:
            cb = agent.circuit_manager.get_breaker(ep_id)
            for _ in range(10):
                cb.report_failure()

        mock = _make_mock_adapter("openai")

        with patch("proxy.rotator.get_adapter", return_value=mock):
            with pytest.raises(HTTPException) as exc_info:
                await agent._forward_with_fallback(ctx, target, {}, MagicMock())
            assert exc_info.value.status_code == 503


# ══════════════════════════════════════════════════════
# Connection Errors
# ══════════════════════════════════════════════════════

class TestFallbackConnectionErrors:

    @pytest.mark.asyncio
    async def test_connection_error_triggers_fallback(self):
        """aiohttp connection error on primary triggers fallback."""
        import aiohttp

        agent = FallbackTestAgent(config={
            "endpoints": {
                "openai": {"provider": "openai", "base_url": "https://api.openai.com/v1"},
                "google": {"provider": "google", "base_url": "https://generativelanguage.googleapis.com/v1beta"},
            },
            "fallback_chains": {"gpt-4o": [{"provider": "google", "model": "gemini-2.5-pro"}]},
        })
        ctx = _make_ctx("gpt-4o")
        target = _make_target("openai")

        primary = _make_mock_adapter("openai", error=aiohttp.ClientError("Connection refused"))
        fallback = _make_mock_adapter("google", _ok_response())
        call_count = 0

        def get_adapter_side_effect(provider_type=None, model=""):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return primary
            return fallback

        with patch("proxy.rotator.get_adapter", side_effect=get_adapter_side_effect):
            await agent._forward_with_fallback(ctx, target, {}, MagicMock())

        assert ctx.response.status_code == 200
        assert ctx.metadata.get("_fallback_used") == "google"


# ══════════════════════════════════════════════════════
# Model Restoration
# ══════════════════════════════════════════════════════

class TestFallbackModelHandling:

    @pytest.mark.asyncio
    async def test_model_swapped_on_fallback(self):
        """Body model field is updated to fallback model on success."""
        agent = FallbackTestAgent(config={
            "endpoints": {
                "openai": {"provider": "openai", "base_url": "https://api.openai.com/v1"},
                "anthropic": {"provider": "anthropic", "base_url": "https://api.anthropic.com/v1"},
            },
            "fallback_chains": {"gpt-4o": [{"provider": "anthropic", "model": "claude-sonnet-4-20250514"}]},
        })
        ctx = _make_ctx("gpt-4o")
        target = _make_target("openai")

        primary = _make_mock_adapter("openai", _error_response(503))
        fallback = _make_mock_adapter("anthropic", _ok_response())
        call_count = 0

        def get_adapter_side_effect(provider_type=None, model=""):
            nonlocal call_count
            call_count += 1
            return primary if call_count == 1 else fallback

        with patch("proxy.rotator.get_adapter", side_effect=get_adapter_side_effect):
            await agent._forward_with_fallback(ctx, target, {}, MagicMock())

        assert ctx.body["model"] == "claude-sonnet-4-20250514"

    @pytest.mark.asyncio
    async def test_model_restored_on_total_failure(self):
        """If all providers fail, original model is restored in body."""
        agent = FallbackTestAgent(config={
            "endpoints": {
                "openai": {"provider": "openai", "base_url": "https://api.openai.com/v1"},
                "anthropic": {"provider": "anthropic", "base_url": "https://api.anthropic.com/v1"},
            },
            "fallback_chains": {"gpt-4o": [{"provider": "anthropic", "model": "claude-sonnet-4-20250514"}]},
        })
        ctx = _make_ctx("gpt-4o")
        target = _make_target("openai")
        mock = _make_mock_adapter("openai", _error_response(503))

        with patch("proxy.rotator.get_adapter", return_value=mock):
            with pytest.raises(HTTPException):
                await agent._forward_with_fallback(ctx, target, {}, MagicMock())

        assert ctx.body["model"] == "gpt-4o"


# ══════════════════════════════════════════════════════
# Endpoint Resolution
# ══════════════════════════════════════════════════════

class TestResolveEndpoint:

    def test_resolve_existing_provider(self):
        agent = FallbackTestAgent(config={
            "endpoints": {"anthropic": {"provider": "anthropic", "base_url": "https://api.anthropic.com/v1"}},
        })
        ep = agent._resolve_endpoint_for_provider("anthropic")
        assert ep is not None
        assert ep.provider == "anthropic"
        assert ep.url == "https://api.anthropic.com/v1"

    def test_resolve_missing_provider(self):
        agent = FallbackTestAgent(config={"endpoints": {}})
        ep = agent._resolve_endpoint_for_provider("nonexistent")
        assert ep is None

    def test_resolve_by_name(self):
        agent = FallbackTestAgent(config={
            "endpoints": {"my-openai": {"provider": "openai", "base_url": "https://api.openai.com/v1"}},
        })
        ep = agent._resolve_endpoint_for_provider("openai")
        assert ep is not None


# ══════════════════════════════════════════════════════
# HTTP status codes that trigger fallback
# ══════════════════════════════════════════════════════

class TestFallbackStatusCodes:

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
    async def test_error_status_triggers_fallback(self, status):
        """HTTP 429, 500, 502, 503, 504 all trigger fallback."""
        agent = FallbackTestAgent(config={
            "endpoints": {
                "openai": {"provider": "openai", "base_url": "https://api.openai.com/v1"},
                "anthropic": {"provider": "anthropic", "base_url": "https://api.anthropic.com/v1"},
            },
            "fallback_chains": {"gpt-4o": [{"provider": "anthropic", "model": "claude-sonnet-4-20250514"}]},
        })
        ctx = _make_ctx("gpt-4o")
        target = _make_target("openai")

        primary = _make_mock_adapter("openai", _error_response(status))
        fallback = _make_mock_adapter("anthropic", _ok_response())
        call_count = 0

        def get_adapter_side_effect(provider_type=None, model=""):
            nonlocal call_count
            call_count += 1
            return primary if call_count == 1 else fallback

        with patch("proxy.rotator.get_adapter", side_effect=get_adapter_side_effect):
            await agent._forward_with_fallback(ctx, target, {}, MagicMock())

        assert ctx.response.status_code == 200
        assert ctx.metadata.get("_fallback_used") == "anthropic"
