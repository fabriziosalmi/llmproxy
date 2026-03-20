import time
import logging
from collections import deque
from typing import List

logger = logging.getLogger(__name__)

class LoadPredictor:
    """Predicts future traffic spikes using basic time-series analysis."""
    
    def __init__(self, window_seconds: int = 60, alpha: float = 0.3):
        self.window_seconds = window_seconds
        self.alpha = alpha # Smoothing factor for Exponential Smoothing
        self.requests = deque()
        self.current_rps_ema = 0.0
        self.last_update = time.time()

    def record_request(self):
        """Records a request timestamp and updates EMA."""
        now = time.time()
        self.requests.append(now)
        self._cleanup(now)
        self._update_ema(now)

    def predict_rps(self, horizon_seconds: int = 10) -> float:
        """Predicts the RPS for the next 'horizon' seconds."""
        # Simple projection based on current EMA and trend (simplified)
        return self.current_rps_ema

    def is_spike_imminent(self, threshold_multiplier: float = 2.0) -> bool:
        """Returns True if a traffic spike is predicted relative to baseline."""
        # For demo: if current RPS is 2x the 1-hour average (stub logic)
        return self.current_rps_ema > 5.0 # Example: > 5 requests/sec is a spike

    def _cleanup(self, now: float):
        """Removes timestamps outside the window."""
        while self.requests and self.requests[0] < now - self.window_seconds:
            self.requests.popleft()

    def _update_ema(self, now: float):
        """Calculates current RPS and updates Exponential Moving Average."""
        # Calculate current instantaneous RPS
        recent_count = len(self.requests)
        current_rps = recent_count / self.window_seconds
        
        # EMA update: S_t = alpha * Y_t + (1-alpha) * S_{t-1}
        self.current_rps_ema = (self.alpha * current_rps) + ((1 - self.alpha) * self.current_rps_ema)
        
        if now - self.last_update > 10:
            logger.info(f"LoadPredictor: Current RPS EMA: {self.current_rps_ema:.2f}")
            self.last_update = now
