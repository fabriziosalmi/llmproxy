"""
Trajectory Ring Buffer — in-memory semantic hash tracking for crescent attack detection.

Stores O(1) semantic hashes per session to detect multi-turn prompt escalation patterns.
Purely in-memory — no external dependencies required.
"""

import hashlib
import logging
from collections import defaultdict, deque

logger = logging.getLogger(__name__)


class TrajectoryBuffer:
    """In-memory trajectory ring buffer for session-level threat tracking."""

    def __init__(self, max_turns: int = 5):
        self.MAX_TURNS = max_turns
        self._buffers: dict = defaultdict(lambda: deque(maxlen=max_turns))

    async def add_turn(self, session_id: str, prompt: str, response: str):
        """Record a turn's semantic hash."""
        turn_data = f"{prompt}|||{response}"
        semantic_hash = hashlib.sha256(turn_data.encode()).hexdigest()
        self._buffers[session_id].append(semantic_hash)

    async def get_trajectory(self, session_id: str) -> list:
        """Get recent turn hashes for a session."""
        return list(self._buffers.get(session_id, []))
