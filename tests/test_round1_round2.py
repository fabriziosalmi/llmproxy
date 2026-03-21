"""
Tests for Round 1 & Round 2 features:
  R1.10: /v1/completions legacy endpoint
  R2.2:  o-series reasoning model support
  R2.5:  Model aliases / groups
  R1.9:  Config hot-reload
  R2.9:  Request deduplication
"""

import pytest

from proxy.adapters.openai import OpenAIAdapter, _is_o_series
from core.model_resolver import resolve_model
from core.deduplicator import RequestDeduplicator


# ══════════════════════════════════════════════════════
# R2.2: O-Series Reasoning Models
# ══════════════════════════════════════════════════════

class TestOSeriesDetection:
    def test_o1(self):
        assert _is_o_series("o1") is True

    def test_o1_mini(self):
        assert _is_o_series("o1-mini") is True

    def test_o1_preview(self):
        assert _is_o_series("o1-preview") is True

    def test_o3(self):
        assert _is_o_series("o3") is True

    def test_o3_mini(self):
        assert _is_o_series("o3-mini") is True

    def test_o4_mini(self):
        assert _is_o_series("o4-mini") is True

    def test_gpt4o_is_not_o_series(self):
        assert _is_o_series("gpt-4o") is False

    def test_empty_string(self):
        assert _is_o_series("") is False

    def test_claude_is_not(self):
        assert _is_o_series("claude-sonnet-4") is False


class TestOSeriesTranslation:
    def setup_method(self):
        self.adapter = OpenAIAdapter()

    def test_system_becomes_developer(self):
        body = {
            "model": "o3-mini",
            "messages": [
                {"role": "system", "content": "You are helpful"},
                {"role": "user", "content": "Hi"},
            ],
        }
        _, translated, _ = self.adapter.translate_request("https://api.openai.com/v1", body, {})
        roles = [m["role"] for m in translated["messages"]]
        assert "system" not in roles
        assert "developer" in roles

    def test_max_tokens_to_max_completion_tokens(self):
        body = {"model": "o3-mini", "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 100}
        _, translated, _ = self.adapter.translate_request("https://api.openai.com/v1", body, {})
        assert "max_tokens" not in translated
        assert translated["max_completion_tokens"] == 100

    def test_unsupported_params_removed(self):
        body = {
            "model": "o3-mini",
            "messages": [{"role": "user", "content": "Hi"}],
            "temperature": 0.7,
            "top_p": 0.9,
            "frequency_penalty": 0.5,
        }
        _, translated, _ = self.adapter.translate_request("https://api.openai.com/v1", body, {})
        assert "temperature" not in translated
        assert "top_p" not in translated
        assert "frequency_penalty" not in translated

    def test_non_o_series_untouched(self):
        body = {
            "model": "gpt-4o",
            "messages": [{"role": "system", "content": "Be helpful"}],
            "temperature": 0.7,
        }
        _, translated, _ = self.adapter.translate_request("https://api.openai.com/v1", body, {})
        assert translated["messages"][0]["role"] == "system"
        assert "temperature" in translated


# ══════════════════════════════════════════════════════
# R2.5: Model Aliases / Groups
# ══════════════════════════════════════════════════════

class TestModelAliases:
    def test_alias_resolution(self):
        config = {"model_aliases": {"gpt4": "gpt-4o", "fast": "gpt-4o-mini"}}
        assert resolve_model(config, "gpt4") == "gpt-4o"
        assert resolve_model(config, "fast") == "gpt-4o-mini"

    def test_unknown_passthrough(self):
        config = {"model_aliases": {}}
        assert resolve_model(config, "gpt-4o") == "gpt-4o"

    def test_no_config(self):
        assert resolve_model({}, "gpt-4o") == "gpt-4o"


class TestModelGroups:
    def test_cheapest_strategy(self):
        config = {
            "model_groups": {
                "auto": {
                    "strategy": "cheapest",
                    "models": [
                        {"model": "gpt-4o", "provider": "openai"},
                        {"model": "gemini-2.0-flash", "provider": "google"},
                    ],
                },
            },
        }
        result = resolve_model(config, "auto")
        # gemini-2.0-flash ($0.10) is cheaper than gpt-4o ($2.50)
        assert result == "gemini-2.0-flash"

    def test_random_strategy(self):
        config = {
            "model_groups": {
                "random": {
                    "strategy": "random",
                    "models": [
                        {"model": "gpt-4o", "provider": "openai"},
                        {"model": "claude-sonnet-4-20250514", "provider": "anthropic"},
                    ],
                },
            },
        }
        result = resolve_model(config, "random")
        assert result in ("gpt-4o", "claude-sonnet-4-20250514")

    def test_weighted_strategy(self):
        config = {
            "model_groups": {
                "weighted": {
                    "strategy": "weighted",
                    "models": [
                        {"model": "gpt-4o", "provider": "openai", "weight": 1.0},
                        {"model": "gpt-4o-mini", "provider": "openai", "weight": 0.0},
                    ],
                },
            },
        }
        # Weight 0 for mini → should always pick gpt-4o
        results = set(resolve_model(config, "weighted") for _ in range(20))
        assert results == {"gpt-4o"}

    def test_empty_group_passthrough(self):
        config = {"model_groups": {"empty": {"strategy": "random", "models": []}}}
        assert resolve_model(config, "empty") == "empty"

    def test_alias_takes_priority_over_group(self):
        config = {
            "model_aliases": {"auto": "gpt-4o"},
            "model_groups": {"auto": {"strategy": "cheapest", "models": [{"model": "gemini-2.0-flash"}]}},
        }
        assert resolve_model(config, "auto") == "gpt-4o"


# ══════════════════════════════════════════════════════
# R2.9: Request Deduplication
# ══════════════════════════════════════════════════════

async def _async_value(val):
    return val


class TestRequestDeduplicator:
    @pytest.mark.asyncio
    async def test_first_call_executes(self):
        dedup = RequestDeduplicator()
        result = await dedup.execute_or_wait("key1", _async_value("response"))
        assert result == "response"

    @pytest.mark.asyncio
    async def test_cached_response_returned(self):
        dedup = RequestDeduplicator(ttl_seconds=10)
        await dedup.execute_or_wait("key1", _async_value("first"))
        result = await dedup.execute_or_wait("key1", _async_value("second"))
        assert result == "first"

    @pytest.mark.asyncio
    async def test_different_keys_independent(self):
        dedup = RequestDeduplicator()
        r1 = await dedup.execute_or_wait("key1", _async_value("a"))
        r2 = await dedup.execute_or_wait("key2", _async_value("b"))
        assert r1 == "a"
        assert r2 == "b"

    @pytest.mark.asyncio
    async def test_exception_propagated(self):
        dedup = RequestDeduplicator()

        async def failing():
            raise ValueError("fail")

        with pytest.raises(ValueError, match="fail"):
            await dedup.execute_or_wait("key1", failing())

    @pytest.mark.asyncio
    async def test_stats(self):
        dedup = RequestDeduplicator()
        assert dedup.stats["in_flight"] == 0
        assert dedup.stats["cached"] == 0
        await dedup.execute_or_wait("key1", _async_value("x"))
        assert dedup.stats["cached"] == 1

    def test_cleanup_expired(self):
        dedup = RequestDeduplicator(ttl_seconds=0)
        import time
        dedup._completed["old"] = ("response", time.time() - 1)
        dedup.cleanup_expired()
        assert "old" not in dedup._completed
