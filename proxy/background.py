"""
LLMPROXY — Background Loops.

Standalone async loops for infrastructure tasks:
config watching, write flushing, cache eviction, dedup cleanup.
All follow the same pattern: sleep → try/except → repeat.
"""

import asyncio
import logging
from typing import Any

logger = logging.getLogger("llmproxy.background")


def _iteration_ok(loop: str) -> None:
    """Record that `loop` finished an iteration without raising.

    Called at the END of each try block, never in an except: the point is to
    distinguish "ran and worked" from "ran and swallowed", which is precisely
    what these loops could not express. Every one of them sits in a broad
    exception handler, so a retention purge that fails on every pass logged one
    warning a day and otherwise looked identical to a healthy one — rows simply
    accumulated, which reads as working retention rather than a failure.
    """
    try:
        from core.metrics import MetricsTracker

        MetricsTracker.mark_background_iteration(loop)
    except Exception:  # noqa: BLE001 — telemetry must never kill a loop
        pass


async def config_watch_loop(agent, interval: int = 30):
    """Detect config.yaml changes and hot-reload security subsystems."""
    from core.startup_checks import StartupError, validate_config
    from core.webhooks import WebhookDispatcher
    from core.security import SecurityShield

    while True:
        await asyncio.sleep(interval)
        try:
            new_hash = await asyncio.to_thread(agent._compute_config_hash_sync)
            if new_hash and new_hash != agent._config_hash:
                # Validate BEFORE installing.
                #
                # This loop assigned the parsed file straight onto the agent and
                # then rebuilt the shield, the webhook dispatcher, the
                # circuit-breaker thresholds, the cache settings and the plugin
                # set from it — with no validation at all, while POST
                # /api/v1/config/apply parses, validates, backs up atomically
                # and rolls back on failure. Identical content, two completely
                # different levels of care, and the unguarded path is the one an
                # operator editing a file actually takes.
                #
                # So a config whose api_keys_env named an unset variable left
                # auth enabled with zero valid keys and every request 401ing,
                # where the same content at startup would have refused to boot
                # with a three-step fix; a port outside 1-65535 was accepted
                # because nothing rechecked it; a malformed fallback_chains
                # entry was installed and failed later as a KeyError inside the
                # forwarder.
                candidate = agent._load_config()
                try:
                    validate_config(candidate)
                except StartupError as e:
                    # Keep running what works. Record the hash so a broken file
                    # does not re-log this every interval — the next EDIT has a
                    # different hash and gets re-examined.
                    logger.error(
                        "Config reload REJECTED, keeping the previous config: %s", e
                    )
                    agent._config_hash = new_hash
                    _iteration_ok("config_watch")
                    continue

                old_webhooks = getattr(agent, "webhooks", None)
                agent.config = candidate
                agent._config_hash = new_hash
                agent.webhooks = WebhookDispatcher(agent.config)
                if old_webhooks and old_webhooks is not agent.webhooks:
                    try:
                        await old_webhooks.close()
                    except Exception as e:
                        logger.warning(
                            "Config reload: previous webhook dispatcher close failed: %s",
                            e,
                        )
                prev_assistant = getattr(agent.security, "assistant", None)
                agent.security = SecurityShield(agent.config, assistant=prev_assistant)
                # Reload circuit breaker thresholds on existing breakers
                if hasattr(agent, "circuit_manager"):
                    cb_cfg = agent.config.get("circuit_breaker", {})
                    ft = cb_cfg.get("failure_threshold", 5)
                    rt = cb_cfg.get("recovery_timeout", 60)
                    for cb in agent.circuit_manager._circuits.values():
                        cb.failure_threshold = ft
                        cb.recovery_timeout = rt
                # Reload cache TTL from new config
                if hasattr(agent, "cache_backend"):
                    cache_cfg = agent.config.get("caching", {})
                    agent.cache_backend._ttl = cache_cfg.get("ttl", 3600)
                    sem_cfg = cache_cfg.get("semantic_cache", {})
                    agent.cache_backend._semantic_enabled = sem_cfg.get(
                        "enabled", False
                    )
                    agent.cache_backend._semantic_threshold = sem_cfg.get(
                        "threshold", 0.85
                    )
                # Trigger plugin hot-reload
                if hasattr(agent, "plugin_manager"):
                    agent.plugin_manager.update_runtime_config(agent.config)
                    await agent.plugin_manager.load_plugins()
                # Invalidate model resolver provider cache
                try:
                    from core.model_resolver import invalidate_provider_cache

                    invalidate_provider_cache()
                except ImportError:
                    pass
                # Re-read secrets on the next request. A config reload that
                # changes api_keys_env, or an operator who has just rotated a
                # key, should take effect now rather than at the next restart.
                try:
                    from core.infisical import clear_cache as _clear_secret_cache

                    _clear_secret_cache()
                except ImportError:
                    pass
                logger.info("Config hot-reloaded (security, circuits, cache, plugins)")
            # Signature hot-reload (independent of config hash)
            if hasattr(agent, "signature_store") and agent.signature_store:
                try:
                    reloaded = await asyncio.to_thread(
                        agent.signature_store.reload_if_changed
                    )
                    if reloaded:
                        logger.info("Signatures hot-reloaded from YAML files")
                except Exception as sig_e:
                    logger.warning(
                        f"Signature reload error (keeping old sigs): {sig_e}"
                    )
            _iteration_ok("config_watch")
        except Exception as e:
            logger.warning(f"Config watch error: {e}")


async def write_flush_loop(agent, interval: float = 1.0):
    """Flush pending state writes to SQLite periodically."""
    while True:
        await asyncio.sleep(interval)
        await drain_pending_writes(agent)
        _iteration_ok("write_flush")


async def drain_pending_writes(agent):
    """Drain all pending writes from the queue to the store."""
    writes: list[tuple[str, Any]] = []
    while not agent._pending_writes.empty():
        try:
            writes.append(agent._pending_writes.get_nowait())
        except asyncio.QueueEmpty:
            break
    for key, value in writes:
        try:
            await agent.store.set_state(key, value)
        except Exception as e:
            logger.warning(f"Failed to flush state write {key}: {e}")


async def metrics_history_loop(agent, interval: int = 3600):
    """Q.3 — Snapshot Prometheus counters into the hourly ring buffer.

    The MetricsHistory ring buffer (24 hourly slots by default) feeds the
    KPI sparklines on the Threats / Models / Analytics views. We sample
    once per hour: deltas for cumulative counters (requests / blocked /
    errors / auth_failures) and a gauge for the running daily cost.

    Defensive everywhere: a malformed counter, an exception, or the agent
    not having `total_cost_today` yet must not stall the loop. Logs the
    error and waits for the next tick.
    """
    from core import metrics
    from core.metrics_history import sum_prometheus_counter

    while True:
        await asyncio.sleep(interval)
        try:
            history = getattr(agent, "metrics_history", None)
            if history is None:
                # Agent didn't construct one — bail out, don't crash the loop.
                continue
            history.record_delta(
                "requests", sum_prometheus_counter(metrics.REQUEST_COUNT)
            )
            history.record_delta(
                "blocked", sum_prometheus_counter(metrics.INJECTION_BLOCKED)
            )
            history.record_delta(
                "errors", sum_prometheus_counter(metrics.REQUEST_ERRORS)
            )
            history.record_delta(
                "auth_failures", sum_prometheus_counter(metrics.AUTH_FAILURES)
            )
            history.record_gauge(
                "cost_usd", float(getattr(agent, "total_cost_today", 0.0))
            )
            _iteration_ok("metrics_history")
        except Exception as e:  # noqa: BLE001 — keep ticking
            logger.warning(f"metrics_history snapshot error: {e}")


async def cache_eviction_loop(cache_backend, interval: int = 3600):
    """Evict expired cache entries periodically."""
    while True:
        await asyncio.sleep(interval)
        try:
            deleted = await cache_backend.evict_expired()
            if deleted > 0:
                logger.info(f"Cache eviction: {deleted} entries purged")
            _iteration_ok("cache_eviction")
        except Exception as e:
            logger.error(f"Cache eviction error: {e}")


async def dedup_cleanup_loop(deduplicator, interval: int = 60):
    """Clean expired entries from the request deduplicator."""
    while True:
        await asyncio.sleep(interval)
        try:
            deduplicator.cleanup_expired()
            _iteration_ok("dedup_cleanup")
        except Exception as e:
            logger.debug(f"Dedup cleanup error: {e}")


async def local_discovery_loop(agent, interval: int = 300):
    """Periodically re-probe local + peer OpenAI-compatible endpoints.

    The boot-time auto-discovery only sees peers that are online at startup.
    This loop re-runs the probe every ``interval`` seconds so endpoints that
    come back online (peer reboot, LM Studio restarted, Ollama pulled a new
    model, ...) get picked up without operator intervention.

    Existing entries are kept as-is: the circuit breaker already handles
    transient outages, and removing a registered endpoint mid-flight would
    race in-progress requests. Only *new* responders are injected and
    seeded into the persistence store for UI registry visibility.
    """
    from core.local_probe import discover_local_endpoints

    while True:
        await asyncio.sleep(interval)
        try:
            before = set(agent.config.get("endpoints", {}).keys())
            injected = await discover_local_endpoints(agent.config)
            if injected:
                new_ids = [ep_id for ep_id in injected if ep_id not in before]
                if new_ids:
                    await agent._seed_endpoints_from_config()
                    logger.info(
                        "Re-discovery added %d new endpoint(s): %s",
                        len(new_ids),
                        ", ".join(new_ids),
                    )
            _iteration_ok("local_discovery")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("Local discovery loop failure: %s", e)


async def retention_purge_loop(store, retention_days: int = 90, interval: int = 86400):
    """GDPR: periodically purge audit/spend records older than retention period.

    Runs once per day (default). Configurable via gdpr.retention_days.
    """
    while True:
        await asyncio.sleep(interval)
        try:
            result = store.purge_expired(retention_days)
            if asyncio.iscoroutine(result):
                result = await result
            total = result.get("audit_deleted", 0) + result.get("spend_deleted", 0)
            if total > 0:
                logger.info(
                    f"GDPR retention purge: {result} (retention={retention_days}d)"
                )
            _iteration_ok("retention_purge")
        except Exception as e:
            logger.warning(f"Retention purge error: {e}")


async def smart_router_sync_loop(agent, interval: int = 5):
    """Periodically synchronize local _endpoint_stats with Redis."""
    if not getattr(agent, "redis_client", None):
        return
    while True:
        await asyncio.sleep(interval)
        try:
            from core.endpoint_stats import sync_endpoint_stats_from_redis
            await sync_endpoint_stats_from_redis(agent.redis_client)
            _iteration_ok("smart_router_sync")
        except Exception as e:
            logger.warning(f"Smart router sync loop error: {e}")
