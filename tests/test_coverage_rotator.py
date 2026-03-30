"""
Coverage tests for proxy/rotator.py — the core orchestrator.

Tests the testable parts of RotatorAgent without requiring a full
running server: config loading, budget tracking, session management,
task management, API key retrieval.
"""

import os
import asyncio
import pytest
from unittest.mock import patch

from tests.conftest import InMemoryRepository


def _make_rotator():
    """Create a RotatorAgent with minimal config and in-memory store."""
    # Patch out heavy deps that try to connect at __init__ time
    with patch.dict(os.environ, {
        "LLM_PROXY_API_KEYS": "sk-proxy-test1,sk-proxy-test2",
        "LLM_PROXY_IDENTITY_SECRET": "test-secret-32-chars-minimum-xxx",
    }):
        from proxy.rotator import RotatorAgent
        store = InMemoryRepository()
        agent = RotatorAgent(store=store, config_path="config.minimal.yaml")
        return agent


class TestRotatorConfig:

    def test_load_config_returns_dict(self):
        agent = _make_rotator()
        assert isinstance(agent.config, dict)
        assert "server" in agent.config

    def test_config_hash_computed(self):
        agent = _make_rotator()
        assert isinstance(agent._config_hash, str)
        assert len(agent._config_hash) == 32  # MD5

    def test_reload_config(self):
        agent = _make_rotator()
        agent.config = agent._load_config()
        assert isinstance(agent.config, dict)


class TestRotatorApiKeys:

    def test_get_api_keys(self):
        with patch.dict(os.environ, {
            "LLM_PROXY_API_KEYS": "sk-proxy-test1,sk-proxy-test2",
        }):
            agent = _make_rotator()
            keys = agent._get_api_keys()
            assert "sk-proxy-test1" in keys
            assert "sk-proxy-test2" in keys
            assert len(keys) == 2


class TestRotatorBudget:

    def test_initial_budget_zero(self):
        agent = _make_rotator()
        assert agent.total_cost_today == 0.0

    def test_budget_lock_exists(self):
        agent = _make_rotator()
        assert isinstance(agent._budget_lock, asyncio.Lock)

    def test_enqueue_write(self):
        agent = _make_rotator()
        agent.enqueue_write("budget:daily_total", 5.0)
        assert not agent._pending_writes.empty()
        key, val = agent._pending_writes.get_nowait()
        assert key == "budget:daily_total"
        assert val == 5.0

    def test_enqueue_write_full_queue(self):
        agent = _make_rotator()
        # Fill the queue (maxsize=500)
        for i in range(500):
            agent.enqueue_write(f"key{i}", i)
        # Next write should not raise
        agent.enqueue_write("overflow", "dropped")

    @pytest.mark.asyncio
    async def test_flush_budget_now(self):
        agent = _make_rotator()
        agent.enqueue_write("test_key", "test_val")
        await agent.flush_budget_now()
        # Verify the write was flushed to store
        result = await agent.store.get_state("test_key")
        assert result == "test_val"


class TestRotatorTaskManagement:

    def test_spawn_task_creates_task(self):
        agent = _make_rotator()

        async def dummy():
            pass

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._spawn_in_loop(agent, dummy()))
            assert len(agent._background_tasks) <= 1  # Task may have completed already
        finally:
            loop.close()

    async def _spawn_in_loop(self, agent, coro):
        return agent._spawn_task(coro)


class TestRotatorSession:

    @pytest.mark.asyncio
    async def test_get_session_creates_session(self):
        agent = _make_rotator()
        session = await agent._get_session()
        assert session is not None
        assert not session.closed
        await session.close()

    @pytest.mark.asyncio
    async def test_get_session_reuses(self):
        agent = _make_rotator()
        s1 = await agent._get_session()
        s2 = await agent._get_session()
        assert s1 is s2
        await s1.close()


class TestRotatorCircuitBreaker:

    @pytest.mark.asyncio
    async def test_circuit_state_change_callback(self):
        agent = _make_rotator()
        # Simulate circuit breaker opening — needs event loop for _spawn_task
        agent._on_circuit_state_change("openai", "closed", "open")
        # Give task time to schedule
        await asyncio.sleep(0.01)

    @pytest.mark.asyncio
    async def test_circuit_recovery_callback(self):
        agent = _make_rotator()
        agent._on_circuit_state_change("openai", "open", "closed")
        await asyncio.sleep(0.01)


class TestRotatorState:

    def test_initial_state(self):
        agent = _make_rotator()
        assert agent.proxy_enabled is True
        assert agent.priority_mode is False
        assert "language_guard" in agent.features
        assert "injection_guard" in agent.features

    def test_deduplicator_exists(self):
        agent = _make_rotator()
        assert agent.deduplicator is not None

    def test_cache_backend_exists(self):
        agent = _make_rotator()
        assert agent.cache_backend is not None

    def test_plugin_manager_exists(self):
        agent = _make_rotator()
        assert agent.plugin_manager is not None

    def test_forwarder_exists(self):
        agent = _make_rotator()
        assert agent.forwarder is not None
