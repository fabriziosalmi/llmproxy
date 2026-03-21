"""
Neural Router — Ring 3: Routing & Load Balancing

Selects a healthy upstream endpoint using:
  - Priority steering (when priority_mode=True): highest-priority endpoint wins
  - Round-robin (default): cycles through healthy endpoints for even load distribution

Endpoints are filtered by circuit breaker state before selection.
No external ML/RL dependencies required.
"""

import logging
from core.plugin_engine import PluginContext

logger = logging.getLogger("plugin.neural_router")

# Module-level round-robin index — persists across requests, reset is harmless
_rr_index = 0


async def select_endpoint(ctx: PluginContext):
    """Ring 3: Routing & Load Balancing."""
    global _rr_index

    rotator = ctx.metadata.get("rotator")

    # Fetch verified pool from store
    pool = await rotator.store.get_pool()
    if not pool:
        ctx.error = "No verified endpoints available."
        ctx.stop_chain = True
        return

    # Filter to endpoints with CLOSED/HALF_OPEN circuit breakers
    healthy = [e for e in pool if rotator.circuit_manager.get_breaker(e.id).can_execute()]
    if not healthy:
        ctx.error = "All endpoints offline (circuit OPEN)."
        ctx.stop_chain = True
        return

    # Steering: priority mode = highest-priority first; else round-robin
    if rotator.priority_mode:
        healthy.sort(key=lambda e: e.metadata.get("priority", 0), reverse=True)
        target = healthy[0]
    else:
        target = healthy[_rr_index % len(healthy)]
        _rr_index = (_rr_index + 1) % max(len(healthy), 1)

    ctx.metadata["target_endpoint"] = target
    await rotator._add_log(f"Routing to: {target.id} ({target.url})", level="PROXY")
