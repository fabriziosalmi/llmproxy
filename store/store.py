import json
import os
from typing import List, Optional
from models import LLMEndpoint, EndpointStatus
import logging

logger = logging.getLogger(__name__)

class EndpointStore:
    def __init__(self, storage_path: str = "store/endpoints.json"):
        self.storage_path = storage_path
        self._endpoints: List[LLMEndpoint] = []
        self._load()

    def _load(self):
        if os.path.exists(self.storage_path):
            try:
                with open(self.storage_path, 'r') as f:
                    data = json.load(f)
                    self._endpoints = [LLMEndpoint(**e) for e in data]
            except Exception as e:
                logger.error(f"Failed to load store: {e}")
                self._endpoints = []

    def _save(self):
        try:
            with open(self.storage_path, 'w') as f:
                json.dump([e.dict() for e in self._endpoints], f, indent=2, default=str)
        except Exception as e:
            logger.error(f"Failed to save store: {e}")

    def add_endpoint(self, endpoint: LLMEndpoint):
        # Update if existing, else append
        for i, e in enumerate(self._endpoints):
            if str(e.url) == str(endpoint.url):
                self._endpoints[i] = endpoint
                self._save()
                return
        self._endpoints.append(endpoint)
        self._save()

    def get_by_status(self, status: EndpointStatus) -> List[LLMEndpoint]:
        return [e for e in self._endpoints if e.status == status]

    def get_pool(self) -> List[LLMEndpoint]:
        return self.get_by_status(EndpointStatus.VERIFIED)
