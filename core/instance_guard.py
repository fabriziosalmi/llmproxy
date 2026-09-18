"""Notice when a second instance is running.

The README and the chart both say to run exactly one, and both explain why: the
daily spend total lives in process memory and is persisted by OVERWRITING a
single key, so two processes each enforce the full daily_limit against their
own float and each overwrites the other's total — the fleet can spend a
multiple of the configured budget. Per-session injection-trajectory scoring is
per-process too, so a session split across instances is scored independently
and the multi-turn detector weakens.

The README also says, accurately, that "nothing detects a second instance — the
failure is silent, and arrives as a provider invoice". That is a description of
a gap, not a mitigation: a `kubectl scale --replicas=3`, a `docker compose up
--scale`, or a blue/green deploy briefly running two pods produced no warning,
no metric and no log line.

This registers a heartbeat in Redis and reports what it finds. It deliberately
does NOT refuse to start:

  * a rolling update legitimately runs two instances for a few seconds, and a
    proxy that refused to boot during every deploy would be worse than the
    problem;
  * a crashed instance leaves a key behind, and a stale lock that blocks
    startup turns a recoverable outage into a manual one.

So the contract is: make it loud and measurable, and let the operator decide.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import time
import uuid
from typing import Any, Optional

logger = logging.getLogger("llmproxy.instance_guard")

#: Redis key prefix. One member per live instance.
_KEY_PREFIX = "llmproxy:instance:"

#: How long an instance's registration survives without a refresh. Two missed
#: heartbeats, so a briefly-slow process is not reported as dead.
_TTL_S = 45

#: How often the registration is refreshed.
_HEARTBEAT_S = 15


def _instance_id() -> str:
    """Something an operator can map back to a container or pod."""
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


class InstanceGuard:
    """Registers this process and counts the others."""

    def __init__(self, redis_client: Optional[Any]):
        self.redis = redis_client
        self.instance_id = _instance_id()
        self.peers: list = []
        self._task: Optional[asyncio.Task] = None

    @property
    def enabled(self) -> bool:
        return self.redis is not None

    @property
    def instance_count(self) -> int:
        """This instance plus every peer seen at the last check."""
        return 1 + len(self.peers)

    async def _register(self) -> None:
        assert self.redis is not None  # nosec B101 — guarded by `enabled`
        await self.redis.set(
            f"{_KEY_PREFIX}{self.instance_id}", str(time.time()), ex=_TTL_S
        )

    async def _scan_peers(self) -> list:
        assert self.redis is not None  # nosec B101 — guarded by `enabled`
        keys = []
        async for key in self.redis.scan_iter(match=f"{_KEY_PREFIX}*"):
            name = key.decode() if isinstance(key, bytes) else key
            peer = name[len(_KEY_PREFIX) :]
            if peer != self.instance_id:
                keys.append(peer)
        return keys

    async def check_once(self) -> int:
        """Refresh this instance's registration and count the live peers."""
        if not self.enabled:
            return 1
        await self._register()
        self.peers = await self._scan_peers()
        return self.instance_count

    async def run(self) -> None:
        """Heartbeat loop. Reports on every transition, not every tick."""
        if not self.enabled:
            logger.debug(
                "Instance guard inactive: no Redis configured, so a second "
                "instance cannot be detected."
            )
            return

        last_count = 0
        while True:
            try:
                count = await self.check_once()
                if count != last_count:
                    if count > 1:
                        logger.error(
                            "MULTIPLE INSTANCES DETECTED: %d running (%s). The "
                            "daily budget is per-process and persisted by "
                            "overwrite, so the fleet can spend a multiple of "
                            "budget.daily_limit, and per-session injection "
                            "scoring is weakened. Peers: %s",
                            count,
                            self.instance_id,
                            ", ".join(self.peers),
                        )
                    else:
                        logger.info("Instance guard: single instance (%s)", self.instance_id)
                    last_count = count
                try:
                    from core.metrics import MetricsTracker

                    MetricsTracker.set_instance_count(count)
                except Exception:  # noqa: BLE001 — telemetry never kills the loop
                    pass
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — a Redis blip is not fatal
                logger.warning("Instance guard check failed: %s", e)
            await asyncio.sleep(_HEARTBEAT_S)

    async def deregister(self) -> None:
        """Remove this instance's key so a clean shutdown does not linger."""
        if not self.enabled or self.redis is None:
            return
        try:
            await self.redis.delete(f"{_KEY_PREFIX}{self.instance_id}")
        except Exception as e:  # noqa: BLE001
            logger.debug("Instance guard deregistration skipped: %s", e)
