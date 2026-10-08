"""What each route module needs from the orchestrator, written down.

Every route module used to be built from the entire ProxyOrchestrator
(``create_router(agent)``). Across proxy/routes/ that is 11 modules reading 36
distinct ``agent.*`` attributes, several of them private, and nothing said which
module needed which: a route could reach any subsystem, and no test could build
one without constructing the full orchestrator.

This file is that statement. Each capability below is one small Protocol; each
route module's ``create_router(agent: XAgent)`` names exactly the capabilities it
uses by inheriting them. Three things then hold:

* mypy checks the route body against the declared surface, and checks that the
  real ProxyOrchestrator provides it (create_app is typed to the orchestrator);
* tests/test_route_dependencies.py fails when a route reads an ``agent``
  attribute its Protocol does not declare, so a new dependency shows up as a
  diff in this file, in review, instead of as one more attribute in a closure;
* a test can pass a small fake that satisfies one Protocol rather than a mock
  that satisfies everything.

This does not make the routes independent of the orchestrator, and the private
members (``_add_log``, ``_verify_admin_key`` ...) keep their names: they are
listed here because the routes do use them, which is the honest description.
Narrowing a route further means removing a base class here.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    import aiohttp
    from fastapi import Request

    from core.cache import CacheBackend, NegativeCache
    from core.circuit_breaker import CircuitManager
    from core.export import DatasetExporter
    from core.identity import IdentityManager
    from core.plugin_engine import PluginManager
    from core.rbac import RBACManager
    from core.security import SecurityShield
    from core.webhooks import WebhookDispatcher
    from proxy.event_log import EventLogger
    from store.base import BaseRepository


# ── state ───────────────────────────────────────────────────────────────────


class HasConfig(Protocol):
    config: dict[str, Any]


class HasStore(Protocol):
    store: BaseRepository


class HasSpendToday(Protocol):
    total_cost_today: float


class HasBudgetLock(Protocol):
    _budget_lock: asyncio.Lock


class HasBudgetDate(Protocol):
    _budget_date: str | None


class HasProxyEnabled(Protocol):
    proxy_enabled: bool


class HasTuning(Protocol):
    """Switches an operator flips at runtime (admin only)."""

    priority_mode: bool
    features: dict[str, bool]
    routing_cost_weight: float


class HasConfigReload(Protocol):
    _config_hash: str

    def _load_config(self) -> dict[str, Any]: ...
    def _compute_config_hash_sync(self) -> str: ...


class HasConfigPath(Protocol):
    config_path: str


class HasNegativeCache(Protocol):
    negative_cache: NegativeCache


# ── collaborators ───────────────────────────────────────────────────────────


class HasSecurity(Protocol):
    security: SecurityShield


class HasPluginManager(Protocol):
    plugin_manager: PluginManager


class HasCircuitManager(Protocol):
    circuit_manager: CircuitManager


class HasWebhooks(Protocol):
    webhooks: WebhookDispatcher


class HasExporter(Protocol):
    exporter: DatasetExporter | None


class HasCacheBackend(Protocol):
    cache_backend: CacheBackend


class HasIdentity(Protocol):
    identity: IdentityManager
    rbac: RBACManager


class HasRbac(Protocol):
    rbac: RBACManager


class HasJwtAuthenticator(Protocol):
    jwt_authenticator: Any


class HasEventLogger(Protocol):
    _event_logger: EventLogger


class HasPluginLogger(Protocol):
    logger: Any


class HasDeduplicator(Protocol):
    deduplicator: Any


# ── behaviour ───────────────────────────────────────────────────────────────


class CanLog(Protocol):
    async def _add_log(
        self,
        message: str,
        level: str = "INFO",
        metadata: dict | None = None,
        trace_id: str | None = None,
    ) -> None: ...


class CanSpawn(Protocol):
    def _spawn_task(self, coro: Any) -> asyncio.Task: ...


class VerifiesAdminKeys(Protocol):
    def _verify_admin_key(self, token: str) -> bool: ...


class VerifiesApiKeys(Protocol):
    def _verify_api_key(self, token: str) -> bool: ...


class CanProxy(Protocol):
    async def proxy_request(
        self, request: Request, body: dict[str, Any] | None = None, session_id: str = "default"
    ) -> Any: ...


class CanQueueWrites(Protocol):
    def enqueue_write(self, key: str, value: Any) -> None: ...
    async def flush_budget_now(self) -> None: ...


class HasHttpSession(Protocol):
    async def _get_session(self) -> aiohttp.ClientSession: ...


# ── one per route module ────────────────────────────────────────────────────
# The base classes ARE the dependency list; keep them sorted by capability.


class ModelsAgent(HasConfig, Protocol):
    pass


class CompletionsAgent(HasConfig, HasStore, CanProxy, Protocol):
    pass


class EmbeddingsAgent(HasConfig, HasSecurity, HasBudgetLock, HasHttpSession, Protocol):
    pass


class ChatAgent(
    HasConfig,
    HasStore,
    HasSpendToday,
    HasBudgetLock,
    HasExporter,
    HasWebhooks,
    HasDeduplicator,
    HasProxyEnabled,
    CanProxy,
    CanSpawn,
    CanQueueWrites,
    Protocol,
):
    pass


class GdprAgent(HasConfig, HasStore, VerifiesAdminKeys, Protocol):
    pass


class IdentityAgent(
    HasConfig, HasStore, HasIdentity, CanLog, VerifiesApiKeys, Protocol
):
    pass


class PluginsAgent(
    HasConfig, HasPluginManager, HasPluginLogger, CanLog, VerifiesAdminKeys, Protocol
):
    pass


class RegistryAgent(
    HasConfig,
    HasStore,
    HasCircuitManager,
    HasEventLogger,
    CanLog,
    VerifiesAdminKeys,
    Protocol,
):
    pass


class ConfigAgent(
    HasConfig,
    HasConfigPath,
    HasConfigReload,
    HasSecurity,
    HasWebhooks,
    HasJwtAuthenticator,
    CanLog,
    VerifiesAdminKeys,
    Protocol,
):
    pass


class TelemetryAgent(
    HasConfig,
    HasStore,
    HasCacheBackend,
    HasCircuitManager,
    HasPluginManager,
    HasEventLogger,
    CanLog,
    VerifiesAdminKeys,
    Protocol,
):
    pass


class AdminAgent(
    HasConfig,
    HasConfigReload,
    HasStore,
    HasSpendToday,
    HasBudgetDate,
    HasProxyEnabled,
    HasTuning,
    HasSecurity,
    HasPluginManager,
    HasCircuitManager,
    HasWebhooks,
    HasExporter,
    HasCacheBackend,
    HasNegativeCache,
    HasRbac,
    HasJwtAuthenticator,
    CanLog,
    VerifiesAdminKeys,
    Protocol,
):
    pass


if TYPE_CHECKING:  # pragma: no cover - read by mypy, never executed
    from proxy.rotator import ProxyOrchestrator

    def _every_route_dependency(
        models: ModelsAgent,
        completions: CompletionsAgent,
        embeddings: EmbeddingsAgent,
        chat: ChatAgent,
        gdpr: GdprAgent,
        identity: IdentityAgent,
        plugins: PluginsAgent,
        registry: RegistryAgent,
        config: ConfigAgent,
        telemetry: TelemetryAgent,
        admin: AdminAgent,
    ) -> None: ...

    def _the_orchestrator_provides_them(o: ProxyOrchestrator) -> None:
        """mypy checks every argument: a Protocol the real class does not satisfy
        (a renamed attribute, a changed signature) fails the type-check gate here
        rather than as an AttributeError in a request. Written as a call because
        CI disables the 'assignment' error code, which an annotated assignment
        would use."""
        _every_route_dependency(o, o, o, o, o, o, o, o, o, o, o)
