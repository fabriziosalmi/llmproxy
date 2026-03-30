from abc import ABC, abstractmethod
import logging


class BaseAgent(ABC):
    def __init__(self, name: str):
        self.name = name
        self.logger = logging.getLogger(f"agent.{name}")

    @abstractmethod
    async def run(self):
        pass


# Back-compat alias — old name was misleading (not an ML agent)
BaseOrchestrator = BaseAgent
