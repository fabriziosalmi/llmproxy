import random
import numpy as np
import logging
from typing import Dict, List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

class RLRotator:
    """Uses a Multi-Armed Bandit (MAB) approach to optimize endpoint selection."""
    
    def __init__(self, alpha: float = 1.0, beta: float = 1.0):
        # Alpha/Beta for Beta distribution (Thomson Sampling)
        # key: endpoint_id
        self.stats: Dict[str, Dict[str, float]] = {}
        self.alpha_init = alpha
        self.beta_init = beta

    def get_best_endpoint(self, endpoint_ids: List[str]) -> str:
        """Selects the best endpoint using Thomson Sampling."""
        if not endpoint_ids:
            return None
            
        scores = {}
        for eid in endpoint_ids:
            if eid not in self.stats:
                self.stats[eid] = {"alpha": self.alpha_init, "beta": self.beta_init, "latency": 1.0}
            
            # Sample from Beta distribution
            # Higher alpha = more success, higher beta = more failure
            score = np.random.beta(self.stats[eid]["alpha"], self.stats[eid]["beta"])
            
            # Penalize by latency (normalized)
            latency_factor = 1.0 / (1.0 + self.stats[eid]["latency"])
            scores[eid] = score * latency_factor
            
        return max(scores, key=scores.get)

    def update(self, endpoint_id: str, success: bool, latency: float):
        """Updates the feedback loop for a specific endpoint."""
        if endpoint_id not in self.stats:
            self.stats[endpoint_id] = {"alpha": self.alpha_init, "beta": self.beta_init, "latency": latency}
        
        if success:
            self.stats[endpoint_id]["alpha"] += 1
        else:
            self.stats[endpoint_id]["beta"] += 1
            
        # Exponential moving average for latency
        self.stats[endpoint_id]["latency"] = (0.9 * self.stats[endpoint_id]["latency"]) + (0.1 * latency)
        
        logger.debug(f"RL Update [{endpoint_id}]: success={success}, latency={latency:.3f}")

class ModelRegistry:
    """Maps endpoints to their capabilities/tiers."""
    @staticmethod
    def get_tier(endpoint_metadata: Dict) -> str:
        # Heuristic: large models usually have more metadata or specific keywords
        tags = endpoint_metadata.get("tags", [])
        if any(t in ["gpt-4", "large", "heavy"] for t in tags):
            return "large"
        if any(t in ["gpt-3.5", "smol", "light"] for t in tags):
            return "smol"
        return "mid"
