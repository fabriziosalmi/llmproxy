import time
import json
from core.plugin_engine import PluginContext

async def record(ctx: PluginContext):
    """Ring 5: Background Telemetry & FinOps."""
    rotator = ctx.metadata.get("rotator")

    # 1. Calculate tokens — prefer actual usage from response, fall back to tiktoken/heuristic
    from core.tokenizer import count_messages_tokens, count_tokens
    model_name = ctx.body.get("model", "")

    # Try real usage from response first
    in_tokens = 0
    out_tokens = 0
    if ctx.response and hasattr(ctx.response, 'body'):
        try:
            usage = json.loads(ctx.response.body.decode()).get("usage", {})
            in_tokens = usage.get("prompt_tokens", 0)
            out_tokens = usage.get("completion_tokens", 0)
        except Exception:
            pass

    # Fall back to tiktoken estimation if response didn't include usage
    if not in_tokens:
        in_tokens = count_messages_tokens(ctx.body.get("messages", []), model_name)
    if not out_tokens and ctx.response and hasattr(ctx.response, 'body'):
        try:
            choices = json.loads(ctx.response.body.decode()).get("choices", [])
            if choices:
                content = choices[0].get("message", {}).get("content", "")
                out_tokens = count_tokens(content, model_name)
        except (json.JSONDecodeError, AttributeError, UnicodeDecodeError):
            pass

    # 2. Cost calculation using per-model pricing table
    from core.pricing import estimate_cost
    model_name = ctx.body.get("model", "")
    total_cost = estimate_cost(model_name, in_tokens, out_tokens)

    # 3. Store metrics in context for dashboard
    target = ctx.metadata.get("target_endpoint")
    target_url = str(target.url) if target and hasattr(target, "url") else "unknown"
    ctx.metadata["metrics"] = {
        "timestamp": time.time(),
        "tokens_in": in_tokens,
        "tokens_out": out_tokens,
        "cost_usd": total_cost,
        "model": target_url,
    }

    # 4. Asynchronous logging
    await rotator._add_log(f"FINOPS: Request complete. {in_tokens+out_tokens} tokens consumed. Est. Cost: ${total_cost:.4f}", level="SYSTEM")

    # Trigger UI update (Future: Broadcast via WebSocket/SSE)
    # rotator.broadcast_metrics(ctx.metadata["metrics"])
