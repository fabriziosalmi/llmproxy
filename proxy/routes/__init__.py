"""
Route modules for RotatorAgent.
Split from the original monolithic _setup_routes() in rotator.py (J.6 refactor).
"""
from .admin import create_router as admin_router
from .registry import create_router as registry_router
from .identity import create_router as identity_router
from .plugins import create_router as plugins_router
from .telemetry import create_router as telemetry_router
from .chat import create_router as chat_router

__all__ = [
    "admin_router",
    "registry_router",
    "identity_router",
    "plugins_router",
    "telemetry_router",
    "chat_router",
]
