"""
Active health probing — proactively detect unhealthy endpoints.

Instead of waiting for real requests to fail (reactive circuit breaker),
this background task pings each configured endpoint every PROBE_INTERVAL
seconds with a minimal 1-token request. Failures are reported to the
circuit breaker so unhealthy endpoints are removed from the pool before
any real traffic hits them.
"""

import time
import random
import asyncio
import logging
from typing import Dict, Any

logger = logging.getLogger("llmproxy.health_prober")

PROBE_INTERVAL = 60  # seconds between probe rounds
PROBE_TIMEOUT = 10  # max seconds per probe request

# URL fragments that indicate the endpoint was never actually configured
# (left as a template in config.yaml). Probing these only produces noise.
_UNCONFIGURED_MARKERS = ("{", "}", "CHANGE-ME", "your-resource", "your-deployment")


def _looks_unconfigured(url: str) -> bool:
    return any(m in url for m in _UNCONFIGURED_MARKERS)


class EndpointHealthProber:
    """Background health prober for configured endpoints."""

    def __init__(self, config: Dict[str, Any], circuit_manager, get_session_fn):
        self.config = config
        self.circuit_manager = circuit_manager
        self._get_session = get_session_fn
        self._running = False
        # Track the last-logged state per endpoint so we warn on the transition
        # (OK→FAIL and FAIL→OK) and stay silent on steady-state repeats. This is
        # what keeps a misconfigured/offline endpoint from flooding logs every
        # minute. We also remember the set of endpoints we've already refused
        # to probe so startup-time skips are announced exactly once.
        self._last_state: Dict[str, str] = {}
        self._warned_unprobeable: set[str] = set()

    async def start(self, interval: int = PROBE_INTERVAL):
        """Start the background probe loop with jitter to prevent thundering herd."""
        self._running = True
        # Initial jitter: stagger first probe 0-50% of interval
        await asyncio.sleep(random.uniform(0, interval * 0.5))
        while self._running:
            try:
                await self._probe_all()
            except Exception as e:
                logger.warning(f"Health probe round failed: {e}")
            # Per-round jitter: ±20% of interval to avoid lock-step with other instances
            jittered = interval * random.uniform(0.8, 1.2)
            await asyncio.sleep(jittered)

    def stop(self):
        self._running = False

    async def _probe_all(self):
        """Probe all configured endpoints in parallel."""
        endpoints_cfg = self.config.get("endpoints", {})
        tasks = []

        for ep_name, ep_config in endpoints_cfg.items():
            provider = ep_config.get("provider", ep_name)
            base_url = ep_config.get("base_url", "")
            models = ep_config.get("models", [])

            if not base_url or not models:
                continue

            # Skip endpoints that still look like a template in config.yaml
            # (placeholder '{resource}', 'CHANGE-ME', etc.). Warn once so the
            # operator sees it, then stay silent — no value in hammering DNS.
            if _looks_unconfigured(base_url):
                if ep_name not in self._warned_unprobeable:
                    logger.info(
                        "Probe skip: %s — base_url still has a placeholder "
                        "('%s'). Fill in config.yaml to enable health probes.",
                        ep_name,
                        base_url,
                    )
                    self._warned_unprobeable.add(ep_name)
                continue

            # Skip endpoints where the circuit breaker is already open — the
            # breaker will run its own half-open probe when it is ready.
            cb = await self.circuit_manager.get_breaker(ep_name)
            if getattr(cb, "state", "closed") == "open":
                continue

            # Skip local endpoints unless probe_local is enabled
            probe_local = ep_config.get("probe_local", False)
            if not probe_local and ("localhost" in base_url or "127.0.0.1" in base_url):
                continue

            tasks.append(
                self._probe_one_jittered(ep_name, provider, base_url, models[0])
            )

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _probe_one_jittered(
        self, ep_name: str, provider: str, base_url: str, model: str
    ):
        """Add per-probe jitter (0-5s) to spread requests across time."""
        await asyncio.sleep(random.uniform(0, 5.0))
        await self._probe_one(ep_name, provider, base_url, model)

    async def _probe_one(self, ep_name: str, provider: str, base_url: str, model: str):
        """Probe a single endpoint with a minimal request."""
        from proxy.adapters.registry import get_adapter
        from plugins.default.neural_router import update_endpoint_stats

        adapter = get_adapter(provider)
        cb = await self.circuit_manager.get_breaker(ep_name)

        # Build minimal probe request
        probe_body = {
            "model": model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
            "stream": False,
        }

        # Build headers from env
        import os

        ep_config = self.config.get("endpoints", {}).get(ep_name, {})
        api_key_env = ep_config.get("api_key_env", "")
        api_key = os.environ.get(api_key_env, "") if api_key_env else ""
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        def _record(new_state: str, msg: str, level: int = logging.WARNING) -> None:
            """Log only on state transition — steady-state noise goes to DEBUG."""
            prev = self._last_state.get(ep_name)
            self._last_state[ep_name] = new_state
            if prev != new_state:
                logger.log(level, msg)
            else:
                logger.debug(msg)

        try:
            url, body, hdrs = adapter.translate_request(base_url, probe_body, headers)
            session = await self._get_session()
            start = time.perf_counter()
            response = await asyncio.wait_for(
                adapter.request(url, body, hdrs, session),
                timeout=PROBE_TIMEOUT,
            )
            latency_ms = (time.perf_counter() - start) * 1000

            if response.status_code < 400:
                await cb.report_success()
                await update_endpoint_stats(ep_name, latency_ms, True)
                _record("ok", f"Probe OK: {ep_name} ({latency_ms:.0f}ms)", logging.INFO)
            else:
                await cb.report_failure()
                await update_endpoint_stats(ep_name, latency_ms, False)
                _record(
                     f"http:{response.status_code}",
                     f"Probe FAIL: {ep_name} → {response.status_code}",
                )

        except asyncio.TimeoutError:
            await cb.report_failure()
            await update_endpoint_stats(ep_name, PROBE_TIMEOUT * 1000, False)
            _record("timeout", f"Probe TIMEOUT: {ep_name} (>{PROBE_TIMEOUT}s)")
        except Exception as e:
            await cb.report_failure()
            _record(f"error:{type(e).__name__}", f"Probe ERROR: {ep_name} → {e}")
