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


async def config_watch_loop(agent, interval: int = 30):
    """Detect config.yaml changes and hot-reload security subsystems."""
    from core.webhooks import WebhookDispatcher
    from core.security import SecurityShield

    while True:
        await asyncio.sleep(interval)
        try:
            new_hash = await asyncio.to_thread(agent._compute_config_hash_sync)
            if new_hash and new_hash != agent._config_hash:
                agent.config = agent._load_config()
                agent._config_hash = new_hash
                agent.webhooks = WebhookDispatcher(agent.config)
                agent.security = SecurityShield(agent.config)
                logger.info("Config hot-reloaded (file change detected)")
        except Exception as e:
            logger.warning(f"Config watch error: {e}")


async def write_flush_loop(agent, interval: float = 1.0):
    """Flush pending state writes to SQLite periodically."""
    while True:
        await asyncio.sleep(interval)
        await drain_pending_writes(agent)


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


async def cache_eviction_loop(cache_backend, interval: int = 3600):
    """Evict expired cache entries periodically."""
    while True:
        await asyncio.sleep(interval)
        try:
            deleted = await cache_backend.evict_expired()
            if deleted > 0:
                logger.info(f"Cache eviction: {deleted} entries purged")
        except Exception as e:
            logger.error(f"Cache eviction error: {e}")


async def dedup_cleanup_loop(deduplicator, interval: int = 60):
    """Clean expired entries from the request deduplicator."""
    while True:
        await asyncio.sleep(interval)
        try:
            deduplicator.cleanup_expired()
        except Exception as e:
            logger.debug(f"Dedup cleanup error: {e}")


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
                logger.info(f"GDPR retention purge: {result} (retention={retention_days}d)")
        except Exception as e:
            logger.warning(f"Retention purge error: {e}")
