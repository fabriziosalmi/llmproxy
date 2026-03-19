from abc import ABC, abstractmethod
import logging
from .retry import RetryStrategy
from .circuit_breaker import CircuitBreaker
from .fsm import StateMachine, State
from .local_assistant import LocalAssistant

logger = logging.getLogger(__name__)

class BaseAgent(ABC):
    def __init__(self, name: str, initial_state: str = "IDLE"):
        self.name = name
        self.retry = RetryStrategy()
        self.circuit_breaker = CircuitBreaker(name=name)
        self.fsm = StateMachine(name=name, initial_state=initial_state)
        self.logger = logging.getLogger(f"agent.{name}")
        self.local_llm = LocalAssistant()
        self._setup_fsm()

    def _setup_fsm(self):
        """Default FSM setup, override in subclasses."""
        self.fsm.add_state(State("IDLE"))
        self.fsm.add_state(State("RUNNING"))
        self.fsm.add_transition("IDLE", "start", "RUNNING")
        self.fsm.add_transition("RUNNING", "stop", "IDLE")

    @abstractmethod
    async def run(self):
        pass

    async def execute_task(self, task_func, *args, **kwargs):
        """Executes a task with integrated circuit breaking and retries."""
        async def wrapped():
            return await self.retry.execute(task_func, *args, **kwargs)
        
        return await self.circuit_breaker.call(wrapped)
