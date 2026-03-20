import time
import logging
from enum import Enum
from typing import Dict, Any

logger = logging.getLogger(__name__)

class CircuitState(Enum):
    CLOSED = "closed"       # Normal operation
    OPEN = "open"           # Blocked due to failures
    HALF_OPEN = "half_open" # Testing for recovery

class CircuitBreaker:
    """Implements a stateful circuit breaker for an LLM endpoint."""
    
    def __init__(self, name: str = "default", failure_threshold: int = 5, recovery_timeout: int = 60):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_count = 0
        self.state = CircuitState.CLOSED
        self.last_failure_time = 0

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

    def report_success(self):
        """Reports a successful interaction, closing the circuit if it was half-open."""
        self.failure_count = 0
        if self.state != CircuitState.CLOSED:
            logger.info("CircuitBreaker: Success detected. Closing circuit.")
            self.state = CircuitState.CLOSED

    def report_failure(self):
        """Reports a failure, opening the circuit if threshold is reached."""
        self.failure_count += 1
        self.last_failure_time = time.time()
        
        if self.state == CircuitState.HALF_OPEN or self.failure_count >= self.failure_threshold:
            if self.state != CircuitState.OPEN:
                logger.warning(f"CircuitBreaker ({self.name}): Failure threshold ({self.failure_threshold}) reached. Opening circuit.")
                self.state = CircuitState.OPEN

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
    
    def __init__(self):
        self._circuits: Dict[str, CircuitBreaker] = {}

    def get_breaker(self, endpoint_id: str) -> CircuitBreaker:
        if endpoint_id not in self._circuits:
            self._circuits[endpoint_id] = CircuitBreaker(name=endpoint_id)
        return self._circuits[endpoint_id]
