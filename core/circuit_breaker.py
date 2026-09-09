import asyncio
import time
import logging
from enum import Enum
from typing import Dict, Optional, Callable, Any

try:
    import redis.asyncio as redis
except ImportError:
    redis = None  # type: ignore

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


LUA_CHECK_SCRIPT = """
local state_key = KEYS[1]
local last_fail_key = KEYS[2]
local probe_key = KEYS[3]
local timeout = tonumber(ARGV[1])
local now = tonumber(ARGV[2])

local state = redis.call('get', state_key) or 'closed'

if state == 'closed' then
    return 1
elseif state == 'open' then
    local last_fail = tonumber(redis.call('get', last_fail_key) or 0)
    if (now - last_fail) > timeout then
        redis.call('set', state_key, 'half_open')
        redis.call('set', probe_key, '1')
        return 2
    end
    return 0
elseif state == 'half_open' then
    local probe = redis.call('get', probe_key)
    if probe == '1' then
        return 0
    else
        redis.call('set', probe_key, '1')
        return 2
    end
end
return 0
"""

LUA_FAILURE_SCRIPT = """
local state_key = KEYS[1]
local fail_key = KEYS[2]
local last_fail_key = KEYS[3]
local probe_key = KEYS[4]
local threshold = tonumber(ARGV[1])
local now = tonumber(ARGV[2])

redis.call('set', probe_key, '0')
redis.call('set', last_fail_key, tostring(now))
local failures = redis.call('incr', fail_key)

local state = redis.call('get', state_key) or 'closed'

if state == 'half_open' or failures >= threshold then
    if state ~= 'open' then
        redis.call('set', state_key, 'open')
        return 1
    end
end
return 0
"""

LUA_SUCCESS_SCRIPT = """
local state_key = KEYS[1]
local fail_key = KEYS[2]
local probe_key = KEYS[3]

redis.call('set', fail_key, '0')
redis.call('set', probe_key, '0')

local state = redis.call('get', state_key) or 'closed'
if state ~= 'closed' then
    redis.call('set', state_key, 'closed')
    return 1
end
return 0
"""


class BaseCircuitBreaker:
    async def can_execute(self) -> bool:
        raise NotImplementedError

    async def report_success(self):
        raise NotImplementedError

    async def report_failure(self):
        raise NotImplementedError

    async def get_state_info(self) -> dict:
        raise NotImplementedError

    async def call(self, func, *args, **kwargs):
        if not await self.can_execute():
            raise Exception(f"Circuit {getattr(self, 'name', 'unknown')} is OPEN. Blocking execution.")
        try:
            result = await func(*args, **kwargs)
            await self.report_success()
            return result
        except Exception as e:
            await self.report_failure()
            logger.error(f"CircuitBreaker ({getattr(self, 'name', 'unknown')}) caught error: {e}")
            raise e


class LocalCircuitBreaker(BaseCircuitBreaker):
    def __init__(
        self,
        name: str = "default",
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
        on_state_change: Optional[Callable[[str, str, str], None]] = None,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_count = 0
        self.state = CircuitState.CLOSED
        self.last_failure_time: float = 0
        self._on_state_change = on_state_change
        self._lock = asyncio.Lock()
        self._half_open_probe_active = False

    async def can_execute(self) -> bool:
        async with self._lock:
            if self.state == CircuitState.CLOSED:
                return True
            if self.state == CircuitState.OPEN:
                if time.time() - self.last_failure_time > self.recovery_timeout:
                    self.state = CircuitState.HALF_OPEN
                    self._half_open_probe_active = True
                    logger.info(f"CircuitBreaker ({self.name}): OPEN → HALF_OPEN, admitting probe.")
                    return True
                return False
            if self.state == CircuitState.HALF_OPEN:
                if self._half_open_probe_active:
                    return False
                self._half_open_probe_active = True
                return True
            return False

    def _notify_state_change(self, old_state: str, new_state: str):
        if self._on_state_change:
            try:
                self._on_state_change(self.name, old_state, new_state)
            except Exception as e:
                logger.error(f"CircuitBreaker state change callback error: {e}")

    async def report_success(self):
        async with self._lock:
            self.failure_count = 0
            self._half_open_probe_active = False
            if self.state != CircuitState.CLOSED:
                old = self.state.value
                self.state = CircuitState.CLOSED
                logger.info(f"CircuitBreaker ({self.name}): Success detected. Closing circuit.")
                self._notify_state_change(old, "closed")

    async def report_failure(self):
        async with self._lock:
            self.failure_count += 1
            self.last_failure_time = time.time()
            self._half_open_probe_active = False
            if self.state == CircuitState.HALF_OPEN or self.failure_count >= self.failure_threshold:
                if self.state != CircuitState.OPEN:
                    old = self.state.value
                    self.state = CircuitState.OPEN
                    logger.warning(f"CircuitBreaker ({self.name}): Failure threshold reached. Opening circuit.")
                    self._notify_state_change(old, "open")

    async def get_state_info(self) -> dict:
        return {
            "state": self.state.value,
            "failure_count": self.failure_count,
            "failure_threshold": self.failure_threshold,
            "recovery_timeout": self.recovery_timeout,
            "last_failure_time": self.last_failure_time,
            "backend": "local"
        }


class RedisCircuitBreaker(BaseCircuitBreaker):
    def __init__(
        self,
        redis_client,
        scripts: dict,
        name: str = "default",
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
        on_state_change: Optional[Callable[[str, str, str], None]] = None,
    ):
        self.redis = redis_client
        self.scripts = scripts
        self.name = name
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._on_state_change = on_state_change

        self.k_state = f"cb:{name}:state"
        self.k_fail = f"cb:{name}:fail"
        self.k_last = f"cb:{name}:last"
        self.k_probe = f"cb:{name}:probe"

        self._local_fallback = LocalCircuitBreaker(
            name=name,
            failure_threshold=failure_threshold,
            recovery_timeout=recovery_timeout,
            on_state_change=on_state_change,
        )

    @property
    def failure_threshold(self) -> int:
        return self._failure_threshold

    @failure_threshold.setter
    def failure_threshold(self, val: int):
        self._failure_threshold = val
        if hasattr(self, "_local_fallback"):
            self._local_fallback.failure_threshold = val

    @property
    def recovery_timeout(self) -> int:
        return self._recovery_timeout

    @recovery_timeout.setter
    def recovery_timeout(self, val: int):
        self._recovery_timeout = val
        if hasattr(self, "_local_fallback"):
            self._local_fallback.recovery_timeout = val

    def _notify_state_change(self, old_state: str, new_state: str):
        if self._on_state_change:
            try:
                self._on_state_change(self.name, old_state, new_state)
            except Exception as e:
                logger.error(f"CircuitBreaker state change callback error: {e}")

    async def can_execute(self) -> bool:
        try:
            res = await self.redis.evalsha(
                self.scripts['check'], 3,
                self.k_state, self.k_last, self.k_probe,
                self.recovery_timeout, time.time()
            )
            if res == 2:
                logger.info(f"CircuitBreaker ({self.name}): OPEN → HALF_OPEN, admitting probe.")
                self._notify_state_change("open", "half_open")
                return True
            return res == 1  # type: ignore
        except Exception as e:
            logger.warning(f"Redis CB check failed: {e}. Falling back to local CB.")
            return await self._local_fallback.can_execute()

    async def report_success(self):
        try:
            res = await self.redis.evalsha(
                self.scripts['success'], 3,
                self.k_state, self.k_fail, self.k_probe
            )
            if res == 1:
                logger.info(f"CircuitBreaker ({self.name}): Success detected. Closing circuit.")
                self._notify_state_change("half_open", "closed")
        except Exception as e:
            logger.warning(f"Redis CB success report failed: {e}. Falling back to local CB.")
            await self._local_fallback.report_success()

    async def report_failure(self):
        try:
            res = await self.redis.evalsha(
                self.scripts['failure'], 4,
                self.k_state, self.k_fail, self.k_last, self.k_probe,
                self.failure_threshold, time.time()
            )
            if res == 1:
                logger.warning(f"CircuitBreaker ({self.name}): Failure threshold reached. Opening circuit.")
                self._notify_state_change("closed", "open")
        except Exception as e:
            logger.warning(f"Redis CB failure report failed: {e}. Falling back to local CB.")
            await self._local_fallback.report_failure()

    async def get_state_info(self) -> dict:
        try:
            state, fail, last = await self.redis.mget(self.k_state, self.k_fail, self.k_last)
            return {
                "state": state or "closed",
                "failure_count": int(fail or 0),
                "failure_threshold": self.failure_threshold,
                "recovery_timeout": self.recovery_timeout,
                "last_failure_time": float(last or 0),
                "backend": "redis"
            }
        except Exception:
            local_info = await self._local_fallback.get_state_info()
            local_info["backend"] = "redis_fallback_local"
            return local_info


class CircuitManager:
    def __init__(
        self,
        on_state_change: Optional[Callable[[str, str, str], None]] = None,
        redis_url: Optional[str] = None,
        redis_client: Optional[Any] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        self._circuits: Dict[str, BaseCircuitBreaker] = {}
        self._on_state_change = on_state_change
        self._lock = asyncio.Lock()

        # Thresholds live on the manager as well as on each breaker, for two
        # reasons: filter_executable needs the recovery timeout to decide
        # whether an open circuit is due for a probe without opening one, and
        # a breaker created after startup used to get the hard-coded 5/60
        # defaults until the config watcher next fired and patched it.
        cb_cfg = (config or {}).get("circuit_breaker", {}) or {}
        self.failure_threshold: int = cb_cfg.get("failure_threshold", 5)
        self.recovery_timeout: int = cb_cfg.get("recovery_timeout", 60)

        self.redis_client = redis_client
        self.scripts = {}  # type: ignore
        if self.redis_client is None and redis_url and redis:
            try:
                from core.redis_client import connect as _redis_connect

                # Timeouts: can_execute() issues one evalsha per endpoint on
                # the routing ring, so an untimed client stalls every request.
                self.redis_client = _redis_connect(redis, redis_url, config)
                logger.info(f"CircuitManager using Redis: {redis_url}")
            except Exception as e:
                logger.error(f"Failed to connect to Redis for CB: {e}")
        elif redis_url and not redis:
            logger.warning("Redis URL provided but 'redis' package not installed.")

    async def _init_scripts(self):
        if self.redis_client and not self.scripts:
            self.scripts['check'] = await self.redis_client.script_load(LUA_CHECK_SCRIPT)
            self.scripts['failure'] = await self.redis_client.script_load(LUA_FAILURE_SCRIPT)
            self.scripts['success'] = await self.redis_client.script_load(LUA_SUCCESS_SCRIPT)

    async def get_breaker(self, endpoint_id: str) -> BaseCircuitBreaker:
        if endpoint_id in self._circuits:
            return self._circuits[endpoint_id]
        async with self._lock:
            if endpoint_id not in self._circuits:
                if self.redis_client:
                    await self._init_scripts()
                    self._circuits[endpoint_id] = RedisCircuitBreaker(
                        self.redis_client,
                        self.scripts,
                        name=endpoint_id,
                        failure_threshold=self.failure_threshold,
                        recovery_timeout=self.recovery_timeout,
                        on_state_change=self._on_state_change
                    )
                else:
                    self._circuits[endpoint_id] = LocalCircuitBreaker(
                        name=endpoint_id,
                        failure_threshold=self.failure_threshold,
                        recovery_timeout=self.recovery_timeout,
                        on_state_change=self._on_state_change
                    )
            return self._circuits[endpoint_id]

    async def filter_executable(self, endpoint_ids: list) -> set:
        """Which of `endpoint_ids` would currently admit a request.

        One round trip for the whole set instead of one per endpoint. The
        routing ring called `get_breaker(id).can_execute()` in a loop for every
        request, which with Redis is one evalsha each, awaited serially — so
        pre-upstream latency scaled linearly with how many endpoints an operator
        had registered, and the project's own benchmarks put the entire
        deterministic security pipeline at tens of microseconds against a
        millisecond-scale round trip performed N times.

        It is also the right call rather than merely the cheaper one.
        `can_execute` is not a read: its Lua script SETS the half-open probe
        key when the recovery timeout has elapsed, so probing every candidate
        consumed the single probe slot for endpoints that were never going to
        be chosen — and /health and the dashboard did the same on every poll.
        This reads state and decides locally; the winner still goes through
        `can_execute` in the forwarder, which is where a probe should be spent.
        """
        if not endpoint_ids:
            return set()

        if not self.redis_client:
            executable = set()
            for endpoint_id in endpoint_ids:
                breaker = await self.get_breaker(endpoint_id)
                if await breaker.can_execute():
                    executable.add(endpoint_id)
            return executable

        now = time.time()
        keys: list = []
        for endpoint_id in endpoint_ids:
            keys.extend(
                (
                    f"cb:{endpoint_id}:state",
                    f"cb:{endpoint_id}:last",
                )
            )
        try:
            values = await self.redis_client.mget(*keys)
        except Exception as e:
            # Same degradation the per-breaker path takes: a Redis problem
            # must not make every endpoint look unavailable.
            logger.warning(f"Circuit state batch read failed: {e}. Falling back.")
            executable = set()
            for endpoint_id in endpoint_ids:
                breaker = await self.get_breaker(endpoint_id)
                if await breaker.can_execute():
                    executable.add(endpoint_id)
            return executable

        executable = set()
        for i, endpoint_id in enumerate(endpoint_ids):
            state = values[2 * i] or "closed"
            last_failure = float(values[2 * i + 1] or 0)
            if state == "closed":
                executable.add(endpoint_id)
            elif state == "half_open":
                # A probe is already in flight or due; let the forwarder's own
                # can_execute decide whether this request is the probe.
                executable.add(endpoint_id)
            elif state == "open" and (now - last_failure) > self.recovery_timeout:
                # Due to transition. Admitting it here is what makes recovery
                # happen at all — the transition itself is still performed
                # atomically by the Lua script in the forwarder.
                executable.add(endpoint_id)
        return executable

    async def get_all_states(self) -> dict:
        result = {}
        for name, cb in self._circuits.items():
            result[name] = await cb.get_state_info()
        return result
