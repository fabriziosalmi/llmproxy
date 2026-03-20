import json
from core.plugin_engine import PluginContext
from fastapi.responses import JSONResponse

async def lookup(ctx: PluginContext):
    """Ring 2: Pre-Flight Semantic Cache Hook."""
    rotator = ctx.metadata.get("rotator")
    body = ctx.body
    
    if not rotator.semantic_cache:
        return

    prompt = body["messages"][-1].get("content", "")
    cached_response = await rotator.semantic_cache.get(prompt)
    
    if cached_response:
        ctx.response = JSONResponse(content=cached_response, status_code=200)
        ctx.stop_chain = True
        await rotator._add_log(f"CACHE HIT: Serving semantically similar response for '{prompt[:30]}...'", level="SYSTEM")
