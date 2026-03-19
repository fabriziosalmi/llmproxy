import asyncio
import time
from enum import Enum
import logging

logger = logging.getLogger(__name__)

class CircuitState(Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"

class CircuitBreaker:
    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        half_open_max_calls: int = 3
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls
        
        self.state = CircuitState.CLOSED
        self.failures = 0
        self.last_failure_time = 0
        self.half_open_calls = 0
        self._lock = asyncio.Lock()

    async def call(self, func, *args, **kwargs):
        async with self._lock:
            await self._before_call()
        
        try:
            result = await func(*args, **kwargs)
            async with self._lock:
                await self._on_success()
            return result
        except Exception as e:
            async with self._lock:
                await self._on_failure()
            raise e

    async def _before_call(self):
        if self.state == CircuitState.OPEN:
            if time.time() - self.last_failure_time > self.recovery_timeout:
                logger.info(f"Circuit {self.name} moving to HALF_OPEN")
                self.state = CircuitState.HALF_OPEN
                self.half_open_calls = 0
            else:
                raise Exception(f"Circuit {self.name} is OPEN. Blocking request.")
        
        if self.state == CircuitState.HALF_OPEN:
            if self.half_open_calls >= self.half_open_max_calls:
                raise Exception(f"Circuit {self.name} is HALF_OPEN and limit reached. Blocking request.")
            self.half_open_calls += 1

    async def _on_success(self):
        if self.state == CircuitState.HALF_OPEN:
            # If we reach certain successful calls in HALF_OPEN, we close it
            # For simplicity, if this call succeeds, we could close it or wait for more
            self.state = CircuitState.CLOSED
            self.failures = 0
            logger.info(f"Circuit {self.name} CLOSED (recovered)")
        elif self.state == CircuitState.CLOSED:
            self.failures = 0

    async def _on_failure(self):
        self.failures += 1
        self.last_failure_time = time.time()
        
        if self.state == CircuitState.CLOSED:
            if self.failures >= self.failure_threshold:
                logger.error(f"Circuit {self.name} OPENED due to {self.failures} failures")
                self.state = CircuitState.OPEN
        elif self.state == CircuitState.HALF_OPEN:
            logger.error(f"Circuit {self.name} returned to OPEN from HALF_OPEN")
            self.state = CircuitState.OPEN
