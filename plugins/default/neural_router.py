"""Back-compat shim — imports from smart_router.py (renamed from neural_router).

All public symbols re-exported so existing imports keep working:
  from plugins.default.neural_router import select_endpoint, update_endpoint_stats, ...
"""
from plugins.default.smart_router import (  # noqa: F401
    update_endpoint_stats,
    get_endpoint_stats,
    select_endpoint,
    _compute_score,
    _endpoint_stats,
    _stats_lock,
    _rr_index,
)
