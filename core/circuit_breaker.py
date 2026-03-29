import asyncio
import time
import logging
from enum import Enum
from typing import Dict, Optional, Callable

logger = logging.getLogger(__name__)

class CircuitState(Enum):
    CLOSED = "closed"       # Normal operation
    OPEN = "open"           # Blocked due to failures
    HALF_OPEN = "half_open" # Testing for recovery

class CircuitBreaker:
    """Implements a stateful circuit breaker for an LLM endpoint.

    All state mutations are protected by an asyncio.Lock to prevent
    concurrent coroutines from corrupting failure_count / state.
    In HALF_OPEN, only a single probe request is admitted; subsequent
    callers are rejected until the probe reports success or failure.
    """

    def __init__(self, name: str = "default", failure_threshold: int = 5, recovery_timeout: int = 60,
                 on_state_change: Optional[Callable[[str, str, str], None]] = None):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_count = 0
        self.state = CircuitState.CLOSED
        self.last_failure_time: float = 0
        self._on_state_change = on_state_change  # callback(name, old_state, new_state)
        self._lock = asyncio.Lock()
        self._half_open_probe_active = False  # True while a probe request is in flight

    async def can_execute(self) -> bool:
        """Checks if the circuit allows execution (async, lock-protected)."""
        async with self._lock:
            if self.state == CircuitState.CLOSED:
                return True

            if self.state == CircuitState.OPEN:
                if time.time() - self.last_failure_time > self.recovery_timeout:
                    self.state = CircuitState.HALF_OPEN
                    self._half_open_probe_active = True
                    logger.info(f"CircuitBreaker ({self.name}): OPEN → HALF_OPEN, admitting probe request.")
                    return True
                return False

            if self.state == CircuitState.HALF_OPEN:
                # Only one probe request at a time in half-open
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
        """Reports a successful interaction, closing the circuit if it was half-open."""
        async with self._lock:
            self.failure_count = 0
            self._half_open_probe_active = False
            if self.state != CircuitState.CLOSED:
                old = self.state.value
                logger.info(f"CircuitBreaker ({self.name}): Success detected. Closing circuit.")
                self.state = CircuitState.CLOSED
                self._notify_state_change(old, "closed")

    async def report_failure(self):
        """Reports a failure, opening the circuit if threshold is reached."""
        async with self._lock:
            self.failure_count += 1
            self.last_failure_time = time.time()
            self._half_open_probe_active = False

            if self.state == CircuitState.HALF_OPEN or self.failure_count >= self.failure_threshold:
                if self.state != CircuitState.OPEN:
                    old = self.state.value
                    logger.warning(f"CircuitBreaker ({self.name}): Failure threshold ({self.failure_threshold}) reached. Opening circuit.")
                    self.state = CircuitState.OPEN
                    self._notify_state_change(old, "open")

    async def call(self, func, *args, **kwargs):
        """Wraps a function call with circuit breaker logic."""
        if not await self.can_execute():
            raise Exception(f"Circuit {self.name} is OPEN. Blocking execution.")

        try:
            result = await func(*args, **kwargs)
            await self.report_success()
            return result
        except Exception as e:
            await self.report_failure()
            logger.error(f"CircuitBreaker ({self.name}) caught error: {e}")
            raise e

class CircuitManager:
    """Manages circuit breakers for all endpoints."""

    def __init__(self, on_state_change: Optional[Callable[[str, str, str], None]] = None):
        self._circuits: Dict[str, CircuitBreaker] = {}
        self._on_state_change = on_state_change

    def get_breaker(self, endpoint_id: str) -> CircuitBreaker:
        if endpoint_id not in self._circuits:
            self._circuits[endpoint_id] = CircuitBreaker(
                name=endpoint_id, on_state_change=self._on_state_change
            )
        return self._circuits[endpoint_id]

    def get_all_states(self) -> dict:
        """Return circuit breaker state for all tracked endpoints."""
        result = {}
        for name, cb in self._circuits.items():
            result[name] = {
                "state": cb.state.value,
                "failure_count": cb.failure_count,
                "failure_threshold": cb.failure_threshold,
                "recovery_timeout": cb.recovery_timeout,
                "last_failure_time": cb.last_failure_time,
            }
        return result
