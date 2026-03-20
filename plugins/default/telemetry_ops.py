import time
import json
from core.plugin_engine import PluginContext

async def record(ctx: PluginContext):
    """Ring 5: Background Telemetry & FinOps."""
    rotator = ctx.metadata.get("rotator")
    
    # 1. Calculate precise tokens (simulated fallback if tiktoken not present)
    prompt_content = json.dumps(ctx.body.get("messages", []))
    response_content = ""
    if ctx.response and hasattr(ctx.response, 'body'):
        response_content = ctx.response.body.decode()

    # Rule of thumb: ~4 chars per token
    in_tokens = len(prompt_content) // 4
    out_tokens = len(response_content) // 4
    
    # 2. Cost calculation (Mock Gpt-4o prices)
    price_in = (in_tokens / 1_000_000) * 5.00
    price_out = (out_tokens / 1_000_000) * 15.00
    total_cost = price_in + price_out

    # 3. Store metrics in context for dashboard
    ctx.metadata["metrics"] = {
        "timestamp": time.time(),
        "tokens_in": in_tokens,
        "tokens_out": out_tokens,
        "cost_usd": total_cost,
        "model": ctx.metadata.get("target_endpoint", "unknown")
    }

    # 4. Asynchronous logging
    await rotator._add_log(f"FINOPS: Request complete. {in_tokens+out_tokens} tokens consumed. Est. Cost: ${total_cost:.4f}", level="SYSTEM")
    
    # Trigger UI update (Future: Broadcast via WebSocket/SSE)
    # rotator.broadcast_metrics(ctx.metadata["metrics"])
