"""
Tests for new marketplace plugins:
  - PromptComplexityScorer
  - ResponseQualityGate
  - LatencySlaGuard
"""

import json
import time
import pytest
from fastapi.responses import Response

from core.plugin_engine import PluginContext, PluginState
from plugins.marketplace.prompt_complexity_scorer import PromptComplexityScorer
from plugins.marketplace.response_quality_gate import ResponseQualityGate
from plugins.marketplace.latency_sla_guard import LatencySlaGuard


# ── Helpers ──

def _make_ctx(messages, metadata=None, response_content=None, state=None):
    """Build a PluginContext with optional response body."""
    ctx = PluginContext(
        body={"messages": messages},
        session_id="test-session",
        metadata=metadata or {},
        state=state or PluginState(),
    )
    if response_content is not None:
        body_data = {
            "choices": [{"message": {"role": "assistant", "content": response_content}}]
        }
        ctx.response = Response(
            content=json.dumps(body_data).encode(),
            status_code=200,
            media_type="application/json",
        )
    return ctx


# ══════════════════════════════════════════════════════════
# Prompt Complexity Scorer Tests
# ══════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_complexity_simple_prompt():
    """Short, single-turn prompt → low complexity."""
    plugin = PromptComplexityScorer()
    ctx = _make_ctx([{"role": "user", "content": "What is 2+2?"}])
    result = await plugin.execute(ctx)

    assert result.action == "passthrough"
    assert ctx.metadata["_prompt_complexity"] < 0.3
    assert ctx.metadata["_complexity_tier"] == "simple"


@pytest.mark.asyncio
async def test_complexity_complex_prompt():
    """Long, multi-turn prompt with code and instructions → high complexity."""
    plugin = PromptComplexityScorer()
    messages = [
        {"role": "system", "content": "You are a senior software architect."},
        {"role": "user", "content": "Explain the differences between microservices and monoliths."},
        {"role": "assistant", "content": "Microservices are distributed..."},
        {"role": "user", "content": (
            "Now implement a microservice in Python that handles user authentication. "
            "First, create the database schema. Second, build the API endpoints. "
            "Third, write unit tests. Finally, optimize the query performance. "
            "Here's the current code:\n"
            "```python\n"
            "class UserService:\n"
            "    def __init__(self, db):\n"
            "        self.db = db\n"
            "    async def create_user(self, name, email):\n"
            "        return await self.db.execute('INSERT INTO users ...')\n"
            "```\n"
            "Make sure to implement proper error handling and analyze the security implications. "
            "Compare this approach with JWT vs session-based auth. "
            "Design the system to handle 10K concurrent users."
        )},
    ]
    ctx = _make_ctx(messages)
    result = await plugin.execute(ctx)

    assert result.action == "passthrough"
    assert ctx.metadata["_prompt_complexity"] > 0.4
    assert ctx.metadata["_complexity_tier"] in ("moderate", "complex")
    assert "depth" in ctx.metadata["_complexity_signals"]
    assert "code" in ctx.metadata["_complexity_signals"]


@pytest.mark.asyncio
async def test_complexity_empty_messages():
    """Empty messages → 0.0 complexity."""
    plugin = PromptComplexityScorer()
    ctx = _make_ctx([])
    result = await plugin.execute(ctx)

    assert result.action == "passthrough"
    assert ctx.metadata["_prompt_complexity"] == 0.0
    assert ctx.metadata["_complexity_tier"] == "simple"


@pytest.mark.asyncio
async def test_complexity_code_heavy():
    """Code-heavy prompt should score high on code signal."""
    plugin = PromptComplexityScorer()
    code = "```python\n" + "x = 1\n" * 50 + "```"
    ctx = _make_ctx([{"role": "user", "content": f"Debug this:\n{code}"}])
    await plugin.execute(ctx)

    assert ctx.metadata["_complexity_signals"]["code"] > 0.3


@pytest.mark.asyncio
async def test_complexity_multi_turn():
    """10-turn conversation should score high on turns signal."""
    plugin = PromptComplexityScorer()
    messages = []
    for i in range(10):
        messages.append({"role": "user", "content": f"Question {i}"})
        messages.append({"role": "assistant", "content": f"Answer {i}"})
    ctx = _make_ctx(messages)
    await plugin.execute(ctx)

    assert ctx.metadata["_complexity_signals"]["turns"] == 1.0


@pytest.mark.asyncio
async def test_complexity_custom_weights():
    """Custom weights should change scoring."""
    plugin = PromptComplexityScorer(config={
        "depth_weight": 1.0,
        "turns_weight": 0.0,
        "code_weight": 0.0,
        "instruction_weight": 0.0,
    })
    ctx = _make_ctx([{"role": "user", "content": "x" * 6000}])
    await plugin.execute(ctx)

    # With 100% depth weight, 6000 chars = 1.0
    assert ctx.metadata["_prompt_complexity"] == 1.0


@pytest.mark.asyncio
async def test_complexity_multimodal_content():
    """Multimodal (list) content should be handled."""
    plugin = PromptComplexityScorer()
    ctx = _make_ctx([{
        "role": "user",
        "content": [
            {"type": "text", "text": "Describe this image in detail"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
        ]
    }])
    result = await plugin.execute(ctx)
    assert result.action == "passthrough"
    assert "_prompt_complexity" in ctx.metadata


# ══════════════════════════════════════════════════════════
# Response Quality Gate Tests
# ══════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_quality_good_response():
    """Normal response → quality 1.0."""
    plugin = ResponseQualityGate()
    ctx = _make_ctx(
        [{"role": "user", "content": "Explain quantum computing"}],
        response_content="Quantum computing uses qubits to perform calculations in parallel, leveraging superposition and entanglement.",
    )
    result = await plugin.execute(ctx)

    assert result.action == "passthrough"
    assert ctx.metadata["_quality_score"] == 1.0
    assert ctx.metadata["_quality_status"] == "ok"


@pytest.mark.asyncio
async def test_quality_empty_response():
    """Empty response → quality 0.0."""
    plugin = ResponseQualityGate()
    ctx = _make_ctx(
        [{"role": "user", "content": "Tell me about Python"}],
        response_content="",
    )
    await plugin.execute(ctx)

    assert ctx.metadata["_quality_score"] == 0.0
    assert ctx.metadata["_quality_status"] == "failed"
    assert "empty_response" in ctx.metadata["_quality_issues"]


@pytest.mark.asyncio
async def test_quality_safety_refusal():
    """Safety refusal → quality 0.2."""
    plugin = ResponseQualityGate()
    ctx = _make_ctx(
        [{"role": "user", "content": "How do I make a sandwich?"}],
        response_content="I cannot assist with that request. As an AI, I'm not able to provide that information. I must decline this request.",
    )
    await plugin.execute(ctx)

    assert ctx.metadata["_quality_score"] == 0.2
    assert ctx.metadata["_quality_status"] == "refused"
    assert "safety_refusal" in ctx.metadata["_quality_issues"]


@pytest.mark.asyncio
async def test_quality_too_short():
    """Ultra-short response to non-trivial prompt → degraded."""
    plugin = ResponseQualityGate()
    ctx = _make_ctx(
        [{"role": "user", "content": "Explain the theory of relativity in detail"}],
        response_content="Yes.",
    )
    await plugin.execute(ctx)

    assert ctx.metadata["_quality_score"] == 0.5
    assert "too_short" in ctx.metadata["_quality_issues"]


@pytest.mark.asyncio
async def test_quality_short_ok_for_trivial():
    """Short response is fine for trivial prompt."""
    plugin = ResponseQualityGate()
    ctx = _make_ctx(
        [{"role": "user", "content": "Hi"}],
        response_content="Hello!",
    )
    await plugin.execute(ctx)

    assert ctx.metadata["_quality_score"] == 1.0
    assert ctx.metadata["_quality_status"] == "ok"


@pytest.mark.asyncio
async def test_quality_truncation_detected():
    """Response ending mid-sentence → truncated."""
    plugin = ResponseQualityGate()
    ctx = _make_ctx(
        [{"role": "user", "content": "Tell me about Python programming"}],
        response_content="Python is a high-level programming language that supports multiple paradigms including object-oriented, functional, and procedural programming. It was created by Guido van",
    )
    await plugin.execute(ctx)

    assert "truncated" in ctx.metadata.get("_quality_issues", [])


@pytest.mark.asyncio
async def test_quality_cached_skip():
    """Cached responses should skip quality check."""
    plugin = ResponseQualityGate()
    ctx = _make_ctx(
        [{"role": "user", "content": "test"}],
        metadata={"_cache_status": "HIT"},
        response_content="",
    )
    await plugin.execute(ctx)

    assert ctx.metadata["_quality_status"] == "cached"
    assert ctx.metadata["_quality_score"] == 1.0


@pytest.mark.asyncio
async def test_quality_apology_only():
    """Apology-only response → degraded."""
    plugin = ResponseQualityGate()
    ctx = _make_ctx(
        [{"role": "user", "content": "Help me with this code"}],
        response_content="I apologize for the confusion.",
    )
    await plugin.execute(ctx)

    assert "apology_only" in ctx.metadata.get("_quality_issues", [])


@pytest.mark.asyncio
async def test_quality_no_response_object():
    """Missing response object → empty."""
    plugin = ResponseQualityGate()
    ctx = PluginContext(
        body={"messages": [{"role": "user", "content": "test"}]},
        session_id="test",
        metadata={},
        state=PluginState(),
    )
    ctx.response = None
    await plugin.execute(ctx)

    assert ctx.metadata["_quality_score"] == 0.0
    assert "empty_response" in ctx.metadata["_quality_issues"]


# ══════════════════════════════════════════════════════════
# Latency SLA Guard Tests
# ══════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_sla_ok():
    """Fast request → SLA ok."""
    plugin = LatencySlaGuard(config={"total_p95_ms": 3000, "hard_limit_ms": 10000})
    now = time.time()
    ctx = _make_ctx(
        [{"role": "user", "content": "test"}],
        metadata={"_request_start_time": now - 0.5},  # 500ms ago
    )
    result = await plugin.execute(ctx)

    assert result.action == "passthrough"
    assert ctx.metadata["_sla_status"] == "ok"
    assert ctx.metadata["_latency_ms"] > 0
    assert plugin._total_requests == 1


@pytest.mark.asyncio
async def test_sla_warning():
    """Slow request (above P95 target) → SLA warning."""
    plugin = LatencySlaGuard(config={"total_p95_ms": 1000, "hard_limit_ms": 10000})
    now = time.time()
    ctx = _make_ctx(
        [{"role": "user", "content": "test"}],
        metadata={"_request_start_time": now - 2.0},  # 2000ms ago
    )
    await plugin.execute(ctx)

    assert ctx.metadata["_sla_status"] == "warning"
    assert len(ctx.metadata["_sla_violations"]) > 0
    assert plugin._sla_warnings == 1


@pytest.mark.asyncio
async def test_sla_breach():
    """Very slow request (above hard limit) → SLA breach."""
    plugin = LatencySlaGuard(config={"hard_limit_ms": 5000})
    now = time.time()
    ctx = _make_ctx(
        [{"role": "user", "content": "test"}],
        metadata={"_request_start_time": now - 6.0},  # 6000ms ago
    )
    await plugin.execute(ctx)

    assert ctx.metadata["_sla_status"] == "breach"
    assert plugin._sla_breaches == 1


@pytest.mark.asyncio
async def test_sla_cached_skip():
    """Cached responses should skip SLA check."""
    plugin = LatencySlaGuard()
    ctx = _make_ctx(
        [{"role": "user", "content": "test"}],
        metadata={"_cache_status": "HIT"},
    )
    await plugin.execute(ctx)

    assert ctx.metadata["_sla_status"] == "cached"


@pytest.mark.asyncio
async def test_sla_ttft_tracking():
    """TTFT should be measured when available."""
    plugin = LatencySlaGuard(config={"ttft_p95_ms": 200})
    now = time.time()
    ctx = _make_ctx(
        [{"role": "user", "content": "test"}],
        metadata={
            "_request_start_time": now - 1.0,
            "_ttft_time": now - 0.7,  # TTFT = 300ms
        },
    )
    await plugin.execute(ctx)

    assert ctx.metadata["_ttft_ms"] is not None
    assert ctx.metadata["_ttft_ms"] > 250  # ~300ms


@pytest.mark.asyncio
async def test_sla_stats():
    """Stats should reflect requests and breaches."""
    plugin = LatencySlaGuard(config={"total_p95_ms": 100, "hard_limit_ms": 500})
    now = time.time()

    # One fast request
    ctx1 = _make_ctx([{"role": "user", "content": "a"}], metadata={"_request_start_time": now - 0.05})
    await plugin.execute(ctx1)

    # One slow request (breach)
    ctx2 = _make_ctx([{"role": "user", "content": "b"}], metadata={"_request_start_time": now - 1.0})
    await plugin.execute(ctx2)

    stats = plugin.get_sla_stats()
    assert stats["total_requests"] == 2
    assert stats["sla_breaches"] == 1
    assert stats["breach_rate"] == 0.5
    assert "p50" in stats["total_percentiles"]


@pytest.mark.asyncio
async def test_sla_no_start_time():
    """Missing start time should not crash."""
    plugin = LatencySlaGuard()
    ctx = _make_ctx(
        [{"role": "user", "content": "test"}],
        metadata={},  # No _request_start_time
    )
    result = await plugin.execute(ctx)

    assert result.action == "passthrough"
    assert ctx.metadata["_latency_ms"] == 0.0


@pytest.mark.asyncio
async def test_sla_rolling_window():
    """Window should cap at configured size."""
    plugin = LatencySlaGuard(config={"window_size": 5})
    now = time.time()

    for i in range(10):
        ctx = _make_ctx(
            [{"role": "user", "content": f"req-{i}"}],
            metadata={"_request_start_time": now - (i * 0.1)},
        )
        await plugin.execute(ctx)

    assert len(plugin._total_samples) == 5  # Capped at window_size
    assert plugin._total_requests == 10  # Counter not capped
