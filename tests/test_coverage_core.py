"""
Coverage tests for core modules at 0% coverage.

Targets: startup_checks, event_log, background, zero_trust,
         secrets, base_agent, discovery_utils, app_factory.
"""

import asyncio
import os
import pytest
from unittest.mock import MagicMock, AsyncMock, patch


# ── startup_checks ────────────────────────────────────────────

class TestStartupChecks:

    def _valid_config(self):
        return {
            "server": {
                "port": 8090,
                "auth": {"enabled": True, "api_keys_env": "LLM_PROXY_API_KEYS"},
            },
            "endpoints": {
                "openai": {"provider": "openai", "api_key_env": "OPENAI_API_KEY", "models": ["gpt-4o"]},
            },
            "security": {"max_payload_size_kb": 512},
            "caching": {"enabled": True, "db_path": "cache.db"},
        }

    def test_valid_config_no_errors(self):
        from core.startup_checks import validate_config
        os.environ["LLM_PROXY_API_KEYS"] = "sk-proxy-test123"
        os.environ["OPENAI_API_KEY"] = "sk-proj-realkey123"
        warnings = validate_config(self._valid_config())
        assert isinstance(warnings, list)

    def test_missing_api_keys_raises(self):
        from core.startup_checks import validate_config, StartupError
        os.environ.pop("LLM_PROXY_API_KEYS", None)
        with pytest.raises(StartupError, match="LLM_PROXY_API_KEYS"):
            validate_config(self._valid_config())

    def test_placeholder_api_key_raises(self):
        from core.startup_checks import validate_config, StartupError
        os.environ["LLM_PROXY_API_KEYS"] = "sk-proxy-CHANGE-ME"
        with pytest.raises(StartupError, match="LLM_PROXY_API_KEYS"):
            validate_config(self._valid_config())

    def test_no_endpoints_raises(self):
        from core.startup_checks import validate_config, StartupError
        os.environ["LLM_PROXY_API_KEYS"] = "sk-proxy-test"
        config = self._valid_config()
        config["endpoints"] = {}
        with pytest.raises(StartupError, match="No LLM endpoints"):
            validate_config(config)

    def test_no_active_providers_raises(self):
        from core.startup_checks import validate_config, StartupError
        os.environ["LLM_PROXY_API_KEYS"] = "sk-proxy-test"
        os.environ.pop("OPENAI_API_KEY", None)
        with pytest.raises(StartupError, match="No endpoints have valid"):
            validate_config(self._valid_config())

    def test_auth_disabled_skips_key_check(self):
        from core.startup_checks import validate_config
        os.environ.pop("LLM_PROXY_API_KEYS", None)
        os.environ["OPENAI_API_KEY"] = "sk-proj-real"
        config = self._valid_config()
        config["server"]["auth"]["enabled"] = False
        warnings = validate_config(config)
        assert isinstance(warnings, list)

    def test_invalid_port_raises(self):
        from core.startup_checks import validate_config, StartupError
        os.environ["LLM_PROXY_API_KEYS"] = "sk-proxy-test"
        os.environ["OPENAI_API_KEY"] = "sk-proj-real"
        config = self._valid_config()
        config["server"]["port"] = 99999
        with pytest.raises(StartupError, match="Invalid server port"):
            validate_config(config)

    def test_small_payload_warning(self):
        from core.startup_checks import validate_config
        os.environ["LLM_PROXY_API_KEYS"] = "sk-proxy-test"
        os.environ["OPENAI_API_KEY"] = "sk-proj-real"
        config = self._valid_config()
        config["security"]["max_payload_size_kb"] = 0
        warnings = validate_config(config)
        assert any("max_payload_size_kb" in w for w in warnings)

    def test_caching_no_db_path_warning(self):
        from core.startup_checks import validate_config
        os.environ["LLM_PROXY_API_KEYS"] = "sk-proxy-test"
        os.environ["OPENAI_API_KEY"] = "sk-proj-real"
        config = self._valid_config()
        config["caching"] = {"enabled": True}
        warnings = validate_config(config)
        assert any("db_path" in w for w in warnings)

    def test_run_startup_checks_exits_on_failure(self):
        from core.startup_checks import run_startup_checks
        os.environ.pop("LLM_PROXY_API_KEYS", None)
        config = self._valid_config()
        with pytest.raises(SystemExit):
            run_startup_checks(config)

    def test_run_startup_checks_passes_with_warnings(self):
        from core.startup_checks import run_startup_checks
        os.environ["LLM_PROXY_API_KEYS"] = "sk-proxy-test"
        os.environ["OPENAI_API_KEY"] = "sk-proj-real"
        config = self._valid_config()
        config["security"]["max_payload_size_kb"] = 0
        run_startup_checks(config)  # Should not raise

    def test_ollama_no_auth_counts_as_active(self):
        from core.startup_checks import validate_config
        os.environ["LLM_PROXY_API_KEYS"] = "sk-proxy-test"
        config = self._valid_config()
        config["endpoints"] = {
            "ollama": {"provider": "ollama", "auth_type": "none", "models": ["llama3"]},
        }
        warnings = validate_config(config)
        assert isinstance(warnings, list)


# ── EventLogger ───────────────────────────────────────────────

class TestEventLogger:

    @pytest.mark.asyncio
    async def test_add_log_basic(self):
        from proxy.event_log import EventLogger
        el = EventLogger(log_maxsize=10)
        await el.add_log("test message", level="INFO")
        assert not el.log_queue.empty()
        entry = el.log_queue.get_nowait()
        assert entry["message"] == "test message"
        assert entry["level"] == "INFO"

    @pytest.mark.asyncio
    async def test_add_log_with_trace_id(self):
        from proxy.event_log import EventLogger
        el = EventLogger()
        await el.add_log("traced", trace_id="abc123")
        entry = el.log_queue.get_nowait()
        assert entry["trace_id"] == "abc123"

    @pytest.mark.asyncio
    async def test_log_queue_overflow_drops_oldest(self):
        from proxy.event_log import EventLogger
        el = EventLogger(log_maxsize=2)
        await el.add_log("first")
        await el.add_log("second")
        await el.add_log("third")  # Should drop "first"
        assert el.log_queue.qsize() == 2

    @pytest.mark.asyncio
    async def test_broadcast_event(self):
        from proxy.event_log import EventLogger
        el = EventLogger(telemetry_maxsize=10)
        await el.broadcast_event("test_event", {"key": "val"})
        event = el.telemetry_queue.get_nowait()
        assert event["type"] == "test_event"
        assert event["data"]["key"] == "val"

    @pytest.mark.asyncio
    async def test_telemetry_overflow(self):
        from proxy.event_log import EventLogger
        el = EventLogger(telemetry_maxsize=1)
        await el.broadcast_event("first", {})
        await el.broadcast_event("second", {})  # Drops "first"
        assert el.telemetry_queue.qsize() == 1


# ── Background loops ─────────────────────────────────────────

class TestBackgroundLoops:

    @pytest.mark.asyncio
    async def test_drain_pending_writes(self):
        from proxy.background import drain_pending_writes

        store = AsyncMock()
        agent = MagicMock()
        agent._pending_writes = asyncio.Queue()
        agent._pending_writes.put_nowait(("key1", "val1"))
        agent._pending_writes.put_nowait(("key2", "val2"))
        agent.store = store

        await drain_pending_writes(agent)

        assert store.set_state.await_count == 2
        assert agent._pending_writes.empty()

    @pytest.mark.asyncio
    async def test_drain_empty_queue(self):
        from proxy.background import drain_pending_writes

        agent = MagicMock()
        agent._pending_writes = asyncio.Queue()
        agent.store = AsyncMock()

        await drain_pending_writes(agent)
        assert agent.store.set_state.await_count == 0

    @pytest.mark.asyncio
    async def test_drain_handles_store_error(self):
        from proxy.background import drain_pending_writes

        agent = MagicMock()
        agent._pending_writes = asyncio.Queue()
        agent._pending_writes.put_nowait(("fail_key", "val"))
        agent.store = AsyncMock()
        agent.store.set_state.side_effect = Exception("DB error")

        await drain_pending_writes(agent)  # Should not raise


# ── ZeroTrustManager ─────────────────────────────────────────

class TestZeroTrustManager:

    def test_disabled_returns_empty_headers(self):
        from core.zero_trust import ZeroTrustManager
        zt = ZeroTrustManager({"security": {"zero_trust": {"enabled": False}}})
        assert zt.get_identity_headers() == {}

    def test_disabled_returns_no_ssl(self):
        from core.zero_trust import ZeroTrustManager
        zt = ZeroTrustManager({"security": {"zero_trust": {"enabled": False}}})
        assert zt.get_ssl_context() is None

    def test_enabled_no_cert_returns_no_ssl(self):
        from core.zero_trust import ZeroTrustManager
        with patch.dict(os.environ, {"LLM_PROXY_IDENTITY_SECRET": "test-secret-that-is-at-least-32-bytes-long"}):
            zt = ZeroTrustManager({"security": {"zero_trust": {"enabled": True}}})
            assert zt.get_ssl_context() is None

    def test_enabled_generates_jwt_headers(self):
        from core.zero_trust import ZeroTrustManager
        with patch.dict(os.environ, {"LLM_PROXY_IDENTITY_SECRET": "test-secret-that-is-at-least-32-bytes-long"}):
            zt = ZeroTrustManager({"security": {"zero_trust": {"enabled": True}}})
            headers = zt.get_identity_headers()
            assert "X-Proxy-Identity" in headers
            assert "X-Zero-Trust" in headers
            assert headers["X-Zero-Trust"] == "true"

    @pytest.mark.asyncio
    async def test_tailscale_verify_missing_socket(self):
        from core.zero_trust import ZeroTrustManager
        zt = ZeroTrustManager({"security": {"zero_trust": {"enabled": False}}})
        zt.ts_socket = "/nonexistent/socket"
        result = await zt.verify_tailscale_identity("100.64.0.1")
        assert result["status"] == "unverified"
        assert result["reason"] == "socket_not_found"


# ── app_factory._read_version ────────────────────────────────

class TestAppFactory:

    def test_read_version_returns_string(self):
        from proxy.app_factory import _read_version
        version = _read_version()
        assert isinstance(version, str)
        assert len(version) > 0

    def test_read_version_missing_file_returns_default(self):
        from proxy.app_factory import _read_version
        with patch("builtins.open", side_effect=FileNotFoundError):
            assert _read_version() == "0.0.0"


# ── base_agent ────────────────────────────────────────────────

class TestBaseAgent:

    def test_base_agent_subclass(self):
        from core.base_agent import BaseAgent

        class TestAgent(BaseAgent):
            async def run(self):
                pass

        agent = TestAgent("test-agent")
        assert agent.name == "test-agent"
        assert agent.logger is not None
