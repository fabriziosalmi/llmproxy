import random
from core.plugin_engine import PluginContext
from models import EndpointStatus
from core.rl_rotator import ModelRegistry
from fastapi import HTTPException

async def select_endpoint(ctx: PluginContext):
    """Ring 3: Routing & Load Balancing."""
    rotator = ctx.metadata.get("rotator")
    body = ctx.body
    prompt = body["messages"][-1].get("content", "")
    
    # 1. Classification
    complexity = await rotator.router.classify(prompt)
    preferred_tier = rotator.router.get_preferred_model_tier(complexity)
    ctx.metadata["complexity"] = complexity
    ctx.metadata["tier"] = preferred_tier

    # 2. Pool Selection
    pool = await rotator.store.get_pool()
    if not pool:
        ctx.error = "No verified endpoints available."
        ctx.stop_chain = True
        return

    healthy_pool = [e for e in pool if rotator.circuit_manager.get_breaker(e.id).can_execute()]
    if not healthy_pool:
        ctx.error = "All endpoints are currently offline (Circuit OPEN)."
        ctx.stop_chain = True
        return

    tier_pool = [e for e in healthy_pool if ModelRegistry.get_tier(e.metadata) == preferred_tier]
    active_pool = tier_pool if tier_pool else healthy_pool 

    # 3. Steering
    if rotator.priority_mode:
        active_pool.sort(key=lambda x: x.metadata.get("priority", 0), reverse=True)
        target = active_pool[0]
    else:
        target_id = rotator.rl_rotator.get_best_endpoint([e.id for e in active_pool])
        target = next(e for e in active_pool if e.id == target_id)

    ctx.metadata["target_endpoint"] = target
    await rotator._add_log(f"Routing to: {target.id} ({target.url})", level="PROXY")
