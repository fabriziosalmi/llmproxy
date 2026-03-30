"""
ThreatLedger — Cross-Session Threat Intelligence.

Tracks threat scores aggregated by IP address and API key prefix,
not just session_id. Detects coordinated attacks where the same
actor rotates sessions to evade per-session trajectory analysis.

Design principles:
  - Additive: does not replace SecurityShield.check_session_trajectory()
  - Memory-bounded: LRU eviction on both IP and key ledgers
  - Time-windowed: only recent scores count (configurable TTL)
  - Zero new deps: uses only stdlib + cachetools (already in requirements)
"""

import time
import logging
from typing import Optional
from cachetools import TTLCache

logger = logging.getLogger("llmproxy.threat_ledger")

# ── Defaults ──
_DEFAULT_MAX_ACTORS = 50_000    # Max tracked IPs/keys
_DEFAULT_WINDOW_SECONDS = 600   # 10-minute scoring window
_DEFAULT_TTL = 3600             # Evict actors idle > 1 hour
_DEFAULT_THRESHOLD = 3.0        # Sum of scores in window to trigger block
_DEFAULT_MIN_EVENTS = 3         # Minimum events before blocking (avoid false positives)


class ThreatLedger:
    """Aggregates threat scores across sessions by actor identity (IP/key).

    Usage:
        ledger = ThreatLedger()
        block_reason = ledger.record(ip="1.2.3.4", key_prefix="sk-abc", score=0.8)
        if block_reason:
            # Block the request
    """

    def __init__(
        self,
        max_actors: int = _DEFAULT_MAX_ACTORS,
        window_seconds: int = _DEFAULT_WINDOW_SECONDS,
        ttl: int = _DEFAULT_TTL,
        threshold: float = _DEFAULT_THRESHOLD,
        min_events: int = _DEFAULT_MIN_EVENTS,
    ):
        self.window_seconds = window_seconds
        self.threshold = threshold
        self.min_events = min_events

        # TTLCache: auto-evicts actors idle longer than ttl.
        # Key = actor identifier (IP or key_prefix)
        # Value = list of (score, timestamp) tuples
        self._ip_ledger: TTLCache = TTLCache(maxsize=max_actors, ttl=ttl)
        self._key_ledger: TTLCache = TTLCache(maxsize=max_actors, ttl=ttl)

    def record(
        self,
        ip: str = "",
        key_prefix: str = "",
        score: float = 0.0,
    ) -> Optional[str]:
        """Record a threat score and check if actor exceeds threshold.

        Args:
            ip: Client IP address
            key_prefix: First 8 chars of API key (or full session_id)
            score: Threat score from SecurityShield._calculate_threat_score()

        Returns:
            Block reason string if threshold exceeded, None otherwise.
        """
        now = time.time()

        # Record in both ledgers (IP and key)
        ip_reason = self._record_and_check(self._ip_ledger, ip, score, now) if ip else None
        key_reason = self._record_and_check(self._key_ledger, key_prefix, score, now) if key_prefix else None

        return ip_reason or key_reason

    def _record_and_check(
        self,
        ledger: TTLCache,
        actor: str,
        score: float,
        now: float,
    ) -> Optional[str]:
        """Record score for an actor and check threshold."""
        if not actor:
            return None

        # Initialize or retrieve
        if actor not in ledger:
            ledger[actor] = []

        entries = ledger[actor]
        entries.append((score, now))

        # Trim to window
        cutoff = now - self.window_seconds
        entries[:] = [(s, ts) for s, ts in entries if ts >= cutoff]

        # Check threshold: need minimum events AND sum exceeds threshold
        if len(entries) >= self.min_events:
            total = sum(s for s, _ in entries)
            if total >= self.threshold:
                logger.warning(
                    f"THREAT LEDGER BLOCK: actor={actor[:16]}... "
                    f"score_sum={total:.2f}/{self.threshold:.1f} "
                    f"events={len(entries)} window={self.window_seconds}s"
                )
                return (
                    f"Cross-session threat detected: {len(entries)} suspicious requests "
                    f"from this origin in {self.window_seconds}s"
                )

        return None

    def get_actor_score(self, actor: str) -> dict:
        """Get current threat state for an actor (for API/dashboard)."""
        now = time.time()
        cutoff = now - self.window_seconds

        ip_entries = self._ip_ledger.get(actor, [])
        key_entries = self._key_ledger.get(actor, [])

        ip_recent = [(s, ts) for s, ts in ip_entries if ts >= cutoff]
        key_recent = [(s, ts) for s, ts in key_entries if ts >= cutoff]

        return {
            "ip_score_sum": sum(s for s, _ in ip_recent),
            "ip_events": len(ip_recent),
            "key_score_sum": sum(s for s, _ in key_recent),
            "key_events": len(key_recent),
            "threshold": self.threshold,
            "window_seconds": self.window_seconds,
        }

    @property
    def stats(self) -> dict:
        """Ledger stats for monitoring."""
        return {
            "tracked_ips": len(self._ip_ledger),
            "tracked_keys": len(self._key_ledger),
            "max_actors": self._ip_ledger.maxsize,
            "window_seconds": self.window_seconds,
            "threshold": self.threshold,
        }
