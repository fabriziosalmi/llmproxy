"""
LLMPROXY — Cache Lookup Plugin (PRE_FLIGHT, priority 30)

Exact-match cache lookup using CacheBackend (injected via PluginState.cache).
Runs AFTER PII masking (priority 20), so cache keys are computed on sanitized prompts.

Features:
  - Cache-Control: no-cache header bypass (for debugging)
  - Tenant-isolated keys (api_key as tenant_id)
  - Sets _cache_key in metadata for background write on miss
  - Uses PluginResponse.cache_hit() (proper SDK contract)
"""

import logging
from core.plugin_engine import PluginContext
from core.plugin_sdk import PluginResponse
from fastapi.responses import JSONResponse

logger = logging.getLogger("plugin.cache_check")


async def lookup(ctx: PluginContext):
    """Ring 2: Pre-Flight Exact-Match Cache Lookup."""
    cache = ctx.state.cache if ctx.state else None
    if not cache or not getattr(cache, "_enabled", False):
        return

    # Cache-Control: no-cache bypass (developer debugging)
    cache_control = ctx.metadata.get("_cache_control", "")
    if "no-cache" in cache_control or "no-store" in cache_control:
        ctx.metadata["_cache_bypass"] = True
        logger.debug("Cache bypass: Cache-Control header")
        return

    # Tenant isolation: use api_key or session_id as tenant boundary
    tenant_id = ctx.metadata.get("api_key", ctx.session_id)

    # Lookup
    cached = await cache.get(ctx.body, tenant_id=tenant_id)

    if cached:
        # HIT — store the raw dict for stream_faker to use if needed
        ctx.metadata["_cached_response_data"] = cached
        ctx.metadata["_cache_status"] = "HIT"

        # Build JSON response for non-streaming path
        response = JSONResponse(content=cached, status_code=200)
        response.headers["X-LLMProxy-Cache"] = "HIT"

        rotator = ctx.metadata.get("rotator")
        if rotator:
            await rotator._add_log(
                f"CACHE HIT: {cache.make_key(ctx.body, tenant_id)[:12]}...",
                level="SYSTEM",
            )

        # Use the SDK contract — engine handles stop_chain + metadata
        return PluginResponse.cache_hit(response)

    # MISS — store the cache key for background write after POST_FLIGHT
    ctx.metadata["_cache_key"] = cache.make_key(ctx.body, tenant_id=tenant_id)
    ctx.metadata["_cache_tenant"] = tenant_id
    ctx.metadata["_cache_status"] = "MISS"
