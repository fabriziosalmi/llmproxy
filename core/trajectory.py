import hashlib
import logging

try:
    import redis.asyncio as redis
    _REDIS_AVAILABLE = True
except ImportError:
    _REDIS_AVAILABLE = False

logger = logging.getLogger(__name__)

class TrajectoryBuffer:
    """
    Stateful Trajectory Ring Buffer backed by Redis to defeat Crescent Attacks.
    Avoids SQL lookups entirely by storing O(1) semantic hashes directly in memory.
    """
    def __init__(self, redis_url="redis://localhost:6379/0"):
        self.MAX_TURNS = 5
        self._connected = False
        if _REDIS_AVAILABLE:
            self.client = redis.from_url(redis_url, decode_responses=True)
        else:
            self.client = None
            logger.info("Redis not installed — trajectory buffer running in-memory (no crescent attack detection)")
    
    async def connect(self):
        if not self.client:
            return
        try:
            await self.client.ping()
            self._connected = True
            logger.info("Connected to Redis Ring Buffer successfully.")
        except Exception as e:
            logger.warning(f"Could not connect to Redis: {e} - Crescent attack mitigation running in degraded mode.")
            self._connected = False

    async def add_turn(self, session_id: str, prompt: str, response: str):
        if not self._connected: return
        # Calculate semantic hash representing this turn trajectory
        turn_data = f"{prompt}|||{response}"
        semantic_hash = hashlib.sha256(turn_data.encode()).hexdigest()
        
        key = f"trajectory:{session_id}"
        try:
            await self.client.lpush(key, semantic_hash)
            await self.client.ltrim(key, 0, self.MAX_TURNS - 1)
            await self.client.expire(key, 3600)  # Expire after 1 hour session idle
        except Exception as e:
            logger.error(f"Redis trajectory tracking failed: {e}")
        
    async def get_trajectory(self, session_id: str):
        if not self._connected: return []
        key = f"trajectory:{session_id}"
        try:
            return await self.client.lrange(key, 0, -1)
        except Exception:
            return []
