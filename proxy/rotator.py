"""
LLMProxy — Security Gateway Orchestrator.

ProxyOrchestrator (formerly RotatorAgent) is the core orchestrator: initializes
the security pipeline, wires route modules via the app factory, and handles the
proxy request chain through the 5-ring plugin system with SecurityShield
pre-inspection.

Extracted modules:
  - proxy/app_factory.py   — FastAPI app + middleware + routes
  - proxy/event_log.py     — Log/telemetry queues + DLQ
  - proxy/background.py    — Background loops (config watch, write flush, cache eviction)
  - proxy/forwarder.py     — Upstream forwarding + fallback chain
"""

import os
import asyncio
import logging
import uvicorn
import aiohttp
from typing import Optional, Dict, Any


from core.base_agent import BaseAgent
from core.metrics import MetricsTracker
from core.zero_trust import ZeroTrustManager
from core.rbac import RBACManager
from core.circuit_breaker import CircuitManager
from core.security import SecurityShield
from core.plugin_engine import PluginManager, PluginState
from core.identity import IdentityManager
from core.webhooks import WebhookDispatcher, EventType
from core.export import DatasetExporter
from core.cache import CacheBackend, NegativeCache

from store.base import BaseRepository
from .adapters.registry import get_adapter
from .app_factory import create_app
from .event_log import EventLogger
from .forwarder import RequestForwarder

logger = logging.getLogger("llmproxy.rotator")


class ProxyOrchestrator(BaseAgent):
    """Security gateway orchestrator — routes requests through the plugin pipeline."""

    def __init__(
        self, store: BaseRepository, assistant=None, config_path: str = "config.yaml"
    ):
        super().__init__("rotator")
        self._session: Optional[aiohttp.ClientSession] = None
        self.store = store
        self.config_path = config_path
        self.model_adapter = get_adapter("openai")  # default, overridden per-request
        self.config = self._load_config()
        self._config_hash = self._compute_config_hash_sync()

        # External signature loading (hot-reloaded every 30s)
        self.signature_store = None
        try:
            from core.signature_loader import SignatureStore

            sig_cfg = self.config.get("security", {}).get("signatures", {})
            pricing_cfg = self.config.get("pricing", {})
            self.signature_store = SignatureStore(
                signatures_path=sig_cfg.get("signatures_file", "data/signatures.yaml"),
                corpus_path=sig_cfg.get("corpus_file", "data/injection_corpus.yaml"),
                pricing_path=pricing_cfg.get("pricing_file", "data/pricing.yaml"),
            )
            self.signature_store.load()
        except Exception as e:
            logger.warning(f"External signatures not loaded, using defaults: {e}")

        # Security subsystems
        self.security = SecurityShield(self.config, assistant=assistant)
        self.zt_manager = ZeroTrustManager(self.config)
        self.rbac = RBACManager()
        self.identity = IdentityManager(self.config)
        
        from core.auth.oidc import JWTAuthenticator
        self.jwt_authenticator = JWTAuthenticator(self.config)
        
        cache_cfg = self.config.get("caching", {})
        redis_url = cache_cfg.get("redis_url") or os.environ.get("REDIS_URL")
        self.circuit_manager = CircuitManager(
            on_state_change=self._on_circuit_state_change,
            redis_url=redis_url
        )

        # Alerting & compliance
        self.webhooks = WebhookDispatcher(self.config)
        export_cfg = self.config.get("observability", {}).get("export", {})
        self.exporter = (
            DatasetExporter(
                output_dir=export_cfg.get("output_dir", "exports"),
                scrub=export_cfg.get("scrub_pii", True),
                compress_on_rotate=export_cfg.get("compress_on_rotate", True),
            )
            if export_cfg.get("enabled")
            else None
        )

        # Budget tracking (hydrated from SQLite in setup())
        self.total_cost_today = 0.0
        self._budget_date: str | None = None
        self._budget_lock = asyncio.Lock()
        self._session_lock = asyncio.Lock()
        self._pending_writes: asyncio.Queue = asyncio.Queue(maxsize=500)

        # Strong references for background tasks (prevents GC in Python 3.12+)
        self._background_tasks: set[asyncio.Task] = set()

        # Gateway state
        self.proxy_enabled = True
        self.priority_mode = False
        # Runtime-tunable routing cost weight (0.0=ignore cost, 1.0=full bias).
        # Smart router reads this directly so /api/v1/routing/cost-weight can
        # adjust without a config reload.
        self.routing_cost_weight: float = float(
            self.config.get("routing", {}).get("cost_weight", 0.3)
        )
        self.features = {
            "language_guard": True,
            "injection_guard": True,
            "link_sanitizer": True,
        }

        # Event logging (extracted to proxy/event_log.py)
        self._event_logger = EventLogger()
        self.log_queue = self._event_logger.log_queue  # backward compat
        self.telemetry_queue = self._event_logger.telemetry_queue  # backward compat

        # L1: Negative cache (in-memory WAF drop)
        neg_cfg = self.config.get("caching", {}).get("negative_cache", {})
        self.negative_cache = NegativeCache(
            maxsize=neg_cfg.get("maxsize", 50_000),
            ttl=neg_cfg.get("ttl", 300),
            enabled=self.config.get("caching", {}).get("enabled", True),
        )

        # L2: Positive cache backend (WAF-aware exact-match)
        cache_cfg = self.config.get("caching", {})
        self.cache_backend = CacheBackend(
            db_path=cache_cfg.get("db_path", "cache.db"),
            ttl=cache_cfg.get("ttl", 3600),
            enabled=cache_cfg.get("enabled", True),
            config=cache_cfg,
        )

        # Request deduplication (idempotency key support)
        from core.deduplicator import RequestDeduplicator

        self.deduplicator = RequestDeduplicator(ttl_seconds=300)

        # Plugin engine
        self.plugin_manager = PluginManager(config=self.config)
        self.plugin_state = PluginState(
            cache=self.cache_backend,
            metrics=MetricsTracker,
            config=self.config.get("plugins", {}),
            extra={"store": self.store},
        )

        # Response signing (S2: cryptographic provenance)
        from core.response_signer import ResponseSigner

        signing_cfg = self.config.get("security", {}).get("response_signing", {})
        signing_key = signing_cfg.get("secret") or os.environ.get(
            "LLM_PROXY_SIGNING_KEY", ""
        )
        self.response_signer = ResponseSigner(signing_key)

        # Request forwarder (extracted to proxy/forwarder.py).
        # Pass `config_provider` instead of a snapshot so the forwarder picks
        # up hot-reloaded `endpoints` and `fallback_chains` after the watcher
        # rebinds `self.config`. Without the provider, the forwarder would be
        # permanently stuck on the boot-time config.
        self.forwarder = RequestForwarder(
            config_provider=lambda: self.config,
            circuit_manager=self.circuit_manager,
            budget_lock=self._budget_lock,
            get_session=self._get_session,
            add_log=self._add_log,
            security=self.security,
        )

        self.app = create_app(self)

    # ── Task Management ──

    def _spawn_task(self, coro) -> asyncio.Task:
        """Create a background task with a strong reference to prevent GC."""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    # ── Config & Secrets ──

    def _load_config(self):
        from .config_loader import load_config

        return load_config(self.config_path)

    def _compute_config_hash_sync(self) -> str:
        """Blocking hash — must run via to_thread() from async context."""
        from .config_loader import compute_config_hash

        return compute_config_hash(self.config_path)

    def enqueue_write(self, key: str, value: Any):
        """Non-blocking enqueue of a state write. Logs error if queue is full."""
        try:
            self._pending_writes.put_nowait((key, value))
        except asyncio.QueueFull:
            self.logger.error("Pending writes queue full — budget state write DROPPED")

    async def _seed_endpoints_from_config(self):
        """Register config.yaml endpoints into the persistence store.
        Thin shim — see proxy/seeding.seed_endpoints_from_config."""
        from .seeding import seed_endpoints_from_config

        return await seed_endpoints_from_config(self.config, self.store)

    async def flush_budget_now(self):
        """Immediate flush of pending writes — called on critical budget thresholds."""
        from .background import drain_pending_writes

        await drain_pending_writes(self)

    def _get_api_keys(self) -> list[str]:
        from .auth_helpers import resolve_api_keys

        return resolve_api_keys(self.config)

    def _verify_api_key(self, token: str) -> bool:
        """Constant-time API-key check (see proxy/auth_helpers.verify_api_key)."""
        from .auth_helpers import verify_api_key

        return verify_api_key(token, self._get_api_keys())

    # ── HTTP Session ──

    async def _get_session(self) -> aiohttp.ClientSession:
        # Fast path: session already alive — no lock needed
        if self._session is not None and not self._session.closed:
            return self._session
        # Slow path: create session under lock to prevent duplicate connectors
        async with self._session_lock:
            # Re-check after acquiring lock (another coroutine may have created it)
            if self._session is not None and not self._session.closed:
                return self._session
            from .http_session import build_http_session

            self._session = build_http_session(self.config)
            connector = self._session.connector
            pool_cfg = self.config.get("connection_pool", {})
            self.logger.info(
                f"HTTP pool: max={connector.limit} per_host={connector.limit_per_host} "
                f"keepalive={pool_cfg.get('keepalive_timeout', 30)}s"
            )
            return self._session

    # ── Circuit Breaker Callbacks ──

    def _on_circuit_state_change(self, endpoint: str, old_state: str, new_state: str):
        MetricsTracker.set_circuit_state(endpoint, new_state == "open")
        if new_state == "open":
            self._spawn_task(
                self.webhooks.dispatch(
                    EventType.CIRCUIT_OPEN,
                    {"endpoint": endpoint, "from": old_state, "to": new_state},
                )
            )
        elif old_state == "open" and new_state == "closed":
            self._spawn_task(
                self.webhooks.dispatch(
                    EventType.ENDPOINT_RECOVERED, {"endpoint": endpoint}
                )
            )

    # ── Logging (delegates to EventLogger) ──

    async def _add_log(
        self,
        message: str,
        level: str = "INFO",
        metadata: dict | None = None,
        trace_id: str | None = None,
    ):
        await self._event_logger.add_log(message, level, metadata, trace_id)

    async def broadcast_event(self, event_type: str, data: Dict[str, Any]):
        await self._event_logger.broadcast_event(event_type, data)

    # ── Lifecycle ──

    async def setup(self):
        """Pre-flight: DB init, plugin load, state hydration, cache init."""
        from .background import (
            config_watch_loop,
            write_flush_loop,
            cache_eviction_loop,
            dedup_cleanup_loop,
            retention_purge_loop,
            local_discovery_loop,
            metrics_history_loop,
        )
        from core.metrics_history import MetricsHistory

        # Q.3 — hourly KPI sparkline ring buffer. Constructed before the
        # snapshot loop is spawned so the very first tick has a target.
        self.metrics_history = MetricsHistory(slots=24)

        await self.store.init()
        await self.cache_backend.init()
        await self.plugin_manager.load_plugins()

        # Hydrate persisted state
        self.proxy_enabled = await self.store.get_state("proxy_enabled", True)
        self.priority_mode = await self.store.get_state("priority_mode", False)
        self.routing_cost_weight = float(
            await self.store.get_state("routing:cost_weight", self.routing_cost_weight)
        )

        # N.6 — Apply persisted rate-limit preset (if any) to the live
        # middleware. Done after middleware construction (which happens when
        # FastAPI is built earlier in startup) so the singleton already exists.
        saved_preset = await self.store.get_state("rate_limit:preset", None)
        if saved_preset:
            try:
                from core.rate_limiter import RateLimitMiddleware

                if RateLimitMiddleware.instance is not None:
                    await RateLimitMiddleware.instance.apply_preset(saved_preset)
            except (ValueError, RuntimeError) as e:
                self.logger.warning(
                    f"Persisted rate-limit preset '{saved_preset}' rejected: {e}"
                )
        for f in self.features:
            self.features[f] = await self.store.get_state(
                f"feature_{f}", self.features[f]
            )
            self.security.config[f] = {"enabled": self.features[f]}

        # Budget hydration (daily reset). See proxy/budget.hydrate_daily_total.
        from .budget import hydrate_daily_total

        self.total_cost_today, self._budget_date = await hydrate_daily_total(self.store)

        # Background loops (extracted to proxy/background.py)
        eviction_interval = self.config.get("caching", {}).get(
            "eviction_interval", 3600
        )
        if self.cache_backend._enabled:
            self._spawn_task(cache_eviction_loop(self.cache_backend, eviction_interval))
        self._spawn_task(config_watch_loop(self, 30))
        self._spawn_task(write_flush_loop(self, 0.25))
        # Q.3 — hourly snapshot loop; interval configurable for ops who want
        # finer-grain KPI sparklines (10-min slots = 4h trailing) or coarser.
        history_interval = int(
            self.config.get("metrics", {}).get("history_interval_s", 3600)
        )
        self._spawn_task(metrics_history_loop(self, history_interval))

        # Auto-discover local OpenAI-compatible providers (Ollama, LM Studio,
        # vLLM, LiteLLM) before seeding. Zero-config path for the developer
        # who already has a local provider running — no YAML or .env edits.
        try:
            from core.local_probe import discover_local_endpoints

            discovered = await discover_local_endpoints(self.config)
            if discovered:
                self._discovered_local = discovered
        except Exception as e:
            logger.debug("Local discovery skipped: %s", e)

        # Seed endpoints from config.yaml into the database
        # so they appear in the UI registry and are available for routing.
        await self._seed_endpoints_from_config()

        # Active health probing
        from core.health_prober import EndpointHealthProber

        self._health_prober = EndpointHealthProber(
            self.config, self.circuit_manager, self._get_session
        )
        self._spawn_task(self._health_prober.start())

        # Periodic local/peer re-discovery — catches peers that come back
        # online after the boot probe. Honours the same disable flag as the
        # boot-time scan (LLM_PROXY_LOCAL_DISCOVERY=0 / discovery.local_scan=false).
        disc_cfg = self.config.get("discovery", {}) or {}
        import os as _os

        _disabled = _os.environ.get(
            "LLM_PROXY_LOCAL_DISCOVERY", ""
        ).strip().lower() in ("0", "false", "off", "no")
        if not _disabled and disc_cfg.get("local_scan", True):
            scan_interval = int(disc_cfg.get("scan_interval_s", 300))
            if scan_interval > 0:
                self._spawn_task(local_discovery_loop(self, scan_interval))

        # Dedup cleanup
        self._spawn_task(dedup_cleanup_loop(self.deduplicator, 60))

        # GDPR: automatic data retention purge
        gdpr_cfg = self.config.get("gdpr", {})
        if gdpr_cfg.get("auto_purge", True):
            retention_days = gdpr_cfg.get("retention_days", 90)
            self._spawn_task(retention_purge_loop(self.store, retention_days))

        self.logger.info("Security gateway ready.")

    async def run(self, port: int | None = None):
        if port is None:
            port = self.config.get("server", {}).get("port", 8090)
        host = self.config.get("server", {}).get("host", "0.0.0.0")  # nosec B104
        await self.setup()
        try:
            from core.ready_banner import print_ready_banner

            print_ready_banner(
                self.config,
                bind_host=host,
                bind_port=port,
                firewall_enabled=getattr(self, "firewall_enabled", True),
                firewall_reason=getattr(self, "firewall_disabled_reason", None),
            )
        except Exception as e:  # banner is informational — never fail startup on it
            self.logger.debug("Ready banner skipped: %s", e)
        config = uvicorn.Config(
            self.app,
            host=host,
            port=port,
            log_level="info",
            server_header=False,  # strip `Server: uvicorn` banner; security_headers middleware sets its own
        )
        server = uvicorn.Server(config)
        await server.serve()

    # ── Core Proxy Pipeline ──

    async def proxy_request(
        self, request, body: Dict[str, Any] | None = None, session_id: str = "default"
    ):
        """Run a request through the 5-ring pipeline.
        Thin shim — see proxy/request_pipeline.process_proxy_request."""
        from .request_pipeline import process_proxy_request

        return await process_proxy_request(self, request, body, session_id)


# Back-compat alias — external code and tests import RotatorAgent
RotatorAgent = ProxyOrchestrator
