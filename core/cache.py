"""
LLMPROXY — WAF-Aware Cache System

Two-layer cache architecture for the security gateway:

  L1 — NegativeCache (in-memory TTLCache):
    Blocks repeated attack prompts in <0.1ms before SecurityShield runs.
    Drops automated brute-force attacks at near-zero CPU cost.
    Checked PRE-SecurityShield, written on 403 block.

  L2 — CacheBackend (SQLite/WAL):
    Exact-match response cache for budget savings.
    Tenant-isolated keys, post-flight validation gate.
    Checked in PRE_FLIGHT ring, written in BACKGROUND ring.

Cache key composition (computed AFTER PII masking in PRE_FLIGHT):
  SHA256(tenant_id \x00 model \x00 temperature \x00 messages_json)
"""

import json
import time
import hashlib
import logging
import unicodedata
from typing import Optional, Dict, Any

try:
    import aiosqlite
except ImportError:
    aiosqlite = None  # type: ignore[assignment]

try:
    from cachetools import TTLCache
except ImportError:
    TTLCache = None

logger = logging.getLogger("llmproxy.cache")


# ── L1: Negative Cache (In-Memory WAF Drop) ──


class NegativeCache:
    """In-memory cache of blocked prompt hashes for instant WAF drops.

    When SecurityShield blocks a prompt (injection, policy violation, etc.),
    the raw prompt hash is stored here. Subsequent identical prompts are
    dropped in <0.1ms without re-running the full security pipeline.

    Properties:
      - TTLCache: entries auto-expire after `ttl` seconds (default 300s / 5min)
      - maxsize: hard cap on memory (default 50,000 entries ≈ 4MB)
      - No false positives: uses SHA-256 exact match (not Bloom filter)
      - Zero persistence: RAM-only, cleared on restart (safe — attacker must retry)
    """

    def __init__(self, maxsize: int = 50_000, ttl: int = 300, enabled: bool = True):
        self._enabled = enabled and TTLCache is not None
        self._drops = 0  # Total prompts dropped by negative cache

        if self._enabled:
            self._store: TTLCache = TTLCache(maxsize=maxsize, ttl=ttl)
            logger.info(f"Negative cache initialized: maxsize={maxsize}, TTL={ttl}s")
        else:
            self._store = None
            if enabled and TTLCache is None:
                logger.warning("cachetools not installed — negative cache disabled")

    @staticmethod
    def _hash_prompt(body: Dict[str, Any]) -> str:
        """Hash the raw prompt for negative cache lookup.

        Uses the full messages array (not just last message) to catch
        multi-turn attack patterns. Raw bytes, not canonical JSON —
        we want byte-identical matching only.
        """
        raw = json.dumps(body.get("messages", []), separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def check(self, body: Dict[str, Any]) -> Optional[str]:
        """Check if this prompt was previously blocked.

        Returns the block reason if found, None if clean.
        O(1) lookup, <0.05ms.
        """
        if not self._enabled or self._store is None:
            return None

        h = self._hash_prompt(body)
        reason = self._store.get(h)
        if reason:
            self._drops += 1
            return str(reason)
        return None

    def add(self, body: Dict[str, Any], reason: str):
        """Record a blocked prompt hash.

        Called after SecurityShield returns a block reason.
        """
        if not self._enabled or self._store is None:
            return

        h = self._hash_prompt(body)
        self._store[h] = reason

    def stats(self) -> Dict[str, Any]:
        """Stats for SOC dashboard."""
        if not self._enabled or self._store is None:
            return {"enabled": False}

        return {
            "enabled": True,
            "size": len(self._store),
            "maxsize": self._store.maxsize,
            "drops": self._drops,
            "ttl": int(self._store.ttl),
        }


class CacheBackend:
    """WAF-aware exact-match cache with SQLite/WAL storage."""

    def __init__(self, db_path: str = "cache.db", ttl: int = 3600, enabled: bool = True):
        self._db_path = db_path
        self._ttl = ttl
        self._enabled = enabled
        self._conn: Optional[Any] = None
        # In-memory stats (not persisted — lightweight)
        self._hits = 0
        self._misses = 0

    async def init(self):
        """Create table, enable WAL mode, set pragmas."""
        if not self._enabled:
            logger.info("Cache disabled by config")
            return
        if aiosqlite is None:
            logger.warning("aiosqlite not installed — cache disabled")
            self._enabled = False
            return

        self._conn = await aiosqlite.connect(self._db_path)
        # WAL mode: concurrent reads + non-blocking writes
        await self._conn.execute("PRAGMA journal_mode=WAL")
        # NORMAL sync: safe with WAL, 10x faster than FULL
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        # Enable auto-vacuum in incremental mode for space reclaim
        await self._conn.execute("PRAGMA auto_vacuum=INCREMENTAL")

        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS response_cache (
                cache_key   TEXT PRIMARY KEY,
                response    TEXT NOT NULL,
                model       TEXT DEFAULT '',
                created_at  REAL NOT NULL
            )
        """)
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_cache_created ON response_cache(created_at)"
        )
        await self._conn.commit()
        logger.info(f"Cache initialized: {self._db_path} (TTL={self._ttl}s, WAL mode)")

    # ── Key Generation ──

    @staticmethod
    def make_key(body: Dict[str, Any], tenant_id: str = "") -> str:
        """Build a deterministic, tenant-isolated cache key.

        Key = SHA256(tenant_id \x00 model \x00 temperature \x00 messages_json)

        The null-byte delimiter prevents concatenation collisions:
        tenant="ab" + model="cd" ≠ tenant="abc" + model="d"

        Messages are already PII-masked by the time this runs (PRE_FLIGHT priority 20).

        Unicode normalization (NFKC) is applied to the canonical messages string
        before hashing.  Without it, an attacker can insert invisible characters
        (U+200B, fullwidth letters, combining marks, etc.) to produce a key that
        differs from the "clean" equivalent — either poisoning the cache with a
        different entry or bypassing negative-cache lookups on repeated attacks.
        NFKC maps fullwidth→ASCII, strips invisible modifiers, and folds
        compatibility equivalents, so visually identical queries share one key.
        """
        model = body.get("model", "")
        temperature = str(body.get("temperature", 1.0))
        # Canonical JSON: sorted keys, no whitespace, deterministic
        messages_raw = json.dumps(body.get("messages", []), sort_keys=True, separators=(",", ":"))
        # NFKC normalization: collapse Unicode variants to canonical form
        messages = unicodedata.normalize("NFKC", messages_raw)

        payload = f"{tenant_id}\x00{model}\x00{temperature}\x00{messages}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    # ── Lookup ──

    async def get(self, body: Dict[str, Any], tenant_id: str = "") -> Optional[Dict[str, Any]]:
        """Cache lookup. Returns parsed response dict or None.

        TTL check is done in SQL for efficiency.
        """
        if not self._enabled or self._conn is None:
            return None

        key = self.make_key(body, tenant_id)
        cutoff = time.time() - self._ttl

        async with self._conn.execute(
            "SELECT response FROM response_cache WHERE cache_key = ? AND created_at > ?",
            (key, cutoff),
        ) as cursor:
            row = await cursor.fetchone()

        if row:
            self._hits += 1
            logger.debug(f"Cache HIT: {key[:12]}...")
            try:
                result: Dict[str, Any] = json.loads(row[0])
                return result
            except (json.JSONDecodeError, TypeError):
                logger.warning(f"Cache corruption for key {key[:12]}, ignoring")
                return None

        self._misses += 1
        return None

    # ── Write ──

    async def put(
        self,
        body: Dict[str, Any],
        response_data: Dict[str, Any],
        tenant_id: str = "",
        model: str = "",
    ):
        """Store a validated response in cache.

        Called from BACKGROUND ring only after POST_FLIGHT passed clean.
        Uses INSERT OR REPLACE for idempotency (concurrent writes are safe).
        """
        if not self._enabled or self._conn is None:
            return

        key = self.make_key(body, tenant_id)
        response_json = json.dumps(response_data, separators=(",", ":"))

        await self._conn.execute(
            "INSERT OR REPLACE INTO response_cache (cache_key, response, model, created_at) VALUES (?, ?, ?, ?)",
            (key, response_json, model, time.time()),
        )
        await self._conn.commit()
        logger.debug(f"Cache WRITE: {key[:12]}... (model={model})")

    # ── Eviction ──

    async def evict_expired(self) -> int:
        """Delete entries older than TTL. Returns count deleted.

        Also runs incremental vacuum to reclaim disk space without
        blocking reads (unlike full VACUUM which locks the entire DB).
        """
        if not self._enabled or self._conn is None:
            return 0

        cutoff = time.time() - self._ttl
        cursor = await self._conn.execute(
            "DELETE FROM response_cache WHERE created_at < ?", (cutoff,)
        )
        deleted: int = cursor.rowcount
        # Reclaim up to 100 pages of freed space
        await self._conn.execute("PRAGMA incremental_vacuum(100)")
        await self._conn.commit()

        if deleted > 0:
            logger.info(f"Cache eviction: {deleted} expired entries removed")
        return deleted

    # ── Stats (for SOC dashboard) ──

    async def stats(self) -> Dict[str, Any]:
        """Return cache metrics for the SOC UI."""
        if not self._enabled or self._conn is None:
            return {"enabled": False}

        async with self._conn.execute("SELECT COUNT(*) FROM response_cache") as cursor:
            row = await cursor.fetchone()
            entry_count = row[0] if row else 0

        total = self._hits + self._misses
        hit_ratio = round(self._hits / total, 4) if total > 0 else 0.0

        return {
            "enabled": True,
            "entries": entry_count,
            "hits": self._hits,
            "misses": self._misses,
            "hit_ratio": hit_ratio,
            "ttl": self._ttl,
            "db_path": self._db_path,
        }

    # ── Lifecycle ──

    async def close(self):
        """Graceful shutdown."""
        if self._conn:
            await self._conn.close()
            self._conn = None
            logger.info("Cache connection closed")
