import time
import logging
from enum import Enum
from typing import Dict, Any, Optional, Callable

logger = logging.getLogger(__name__)

class CircuitState(Enum):
    CLOSED = "closed"       # Normal operation
    OPEN = "open"           # Blocked due to failures
    HALF_OPEN = "half_open" # Testing for recovery

class CircuitBreaker:
    """Implements a stateful circuit breaker for an LLM endpoint."""

    def __init__(self, name: str = "default", failure_threshold: int = 5, recovery_timeout: int = 60,
                 on_state_change: Optional[Callable[[str, str, str], None]] = None):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_count = 0
        self.state = CircuitState.CLOSED
        self.last_failure_time = 0
        self._on_state_change = on_state_change  # callback(name, old_state, new_state)

    def can_execute(self) -> bool:
        """Checks if the circuit allows execution."""
        if self.state == CircuitState.CLOSED:
            return True
        
        if self.state == CircuitState.OPEN:
            if time.time() - self.last_failure_time > self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                logger.info("CircuitBreaker: Transitioning to HALF_OPEN for recovery testing.")
                return True
            return False
        
        if self.state == CircuitState.HALF_OPEN:
            # Only allow one request at a time in half_open (simplified)
            return True
            
        return False

    def _notify_state_change(self, old_state: str, new_state: str):
        if self._on_state_change:
            try:
                self._on_state_change(self.name, old_state, new_state)
            except Exception as e:
                logger.error(f"CircuitBreaker state change callback error: {e}")

    def report_success(self):
        """Reports a successful interaction, closing the circuit if it was half-open."""
        self.failure_count = 0
        if self.state != CircuitState.CLOSED:
            old = self.state.value
            logger.info("CircuitBreaker: Success detected. Closing circuit.")
            self.state = CircuitState.CLOSED
            self._notify_state_change(old, "closed")

    def report_failure(self):
        """Reports a failure, opening the circuit if threshold is reached."""
        self.failure_count += 1
        self.last_failure_time = time.time()

        if self.state == CircuitState.HALF_OPEN or self.failure_count >= self.failure_threshold:
            if self.state != CircuitState.OPEN:
                old = self.state.value
                logger.warning(f"CircuitBreaker ({self.name}): Failure threshold ({self.failure_threshold}) reached. Opening circuit.")
                self.state = CircuitState.OPEN
                self._notify_state_change(old, "open")

    async def call(self, func, *args, **kwargs):
        """Wraps a function call with circuit breaker logic."""
        if not self.can_execute():
            raise Exception(f"Circuit {self.name} is OPEN. Blocking execution.")
            
        try:
            result = await func(*args, **kwargs)
            self.report_success()
            return result
        except Exception as e:
            self.report_failure()
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
