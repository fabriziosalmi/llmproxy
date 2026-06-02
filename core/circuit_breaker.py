import asyncio
import time
import logging
from enum import Enum
from typing import Dict, Optional, Callable

try:
    import redis.asyncio as redis
except ImportError:
    redis = None

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
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._on_state_change = on_state_change
        
        self.k_state = f"cb:{name}:state"
        self.k_fail = f"cb:{name}:fail"
        self.k_last = f"cb:{name}:last"
        self.k_probe = f"cb:{name}:probe"

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
            return res == 1
        except Exception as e:
            logger.warning(f"Redis CB check failed: {e}")
            return True

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
            logger.warning(f"Redis CB success report failed: {e}")

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
            logger.warning(f"Redis CB failure report failed: {e}")

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
            return {"state": "unknown", "backend": "redis_error"}


class CircuitManager:
    def __init__(
        self, 
        on_state_change: Optional[Callable[[str, str, str], None]] = None,
        redis_url: Optional[str] = None
    ):
        self._circuits: Dict[str, BaseCircuitBreaker] = {}
        self._on_state_change = on_state_change
        self._lock = asyncio.Lock()
        
        self.redis_client = None
        self.scripts = {}
        if redis_url and redis:
            try:
                self.redis_client = redis.from_url(redis_url, decode_responses=True)
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
                        on_state_change=self._on_state_change
                    )
                else:
                    self._circuits[endpoint_id] = LocalCircuitBreaker(
                        name=endpoint_id, 
                        on_state_change=self._on_state_change
                    )
            return self._circuits[endpoint_id]

    async def get_all_states(self) -> dict:
        result = {}
        for name, cb in self._circuits.items():
            result[name] = await cb.get_state_info()
        return result
