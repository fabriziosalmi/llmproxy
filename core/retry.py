import asyncio
import random
from typing import Callable, Any, List, Optional
import logging

logger = logging.getLogger(__name__)

class RetryStrategy:
    def __init__(
        self,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        exponential_base: float = 2.0,
        jitter: bool = True,
        retryable_exceptions: List[type] = (Exception,),
        retryable_status_codes: Optional[List[int]] = None
    ):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base
        self.jitter = jitter
        self.retryable_exceptions = tuple(retryable_exceptions)
        self.retryable_status_codes = retryable_status_codes or [429, 500, 502, 503, 504]

    async def execute(self, func: Callable, *args, **kwargs) -> Any:
        last_exception = None
        for attempt in range(self.max_retries + 1):
            try:
                result = await func(*args, **kwargs)
                return result
            except self.retryable_exceptions as e:
                last_exception = e
                if attempt == self.max_retries:
                    break
                
                delay = min(self.max_delay, self.base_delay * (self.exponential_base ** attempt))
                if self.jitter:
                    delay = delay * (0.5 + random.random())
                
                logger.warning(f"Attempt {attempt + 1} failed: {e}. Retrying in {delay:.2f}s...")
                await asyncio.sleep(delay)
        
        raise last_exception
