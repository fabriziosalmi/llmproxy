import asyncio
import logging
import os
import importlib.util
import inspect
from typing import List, Dict, Type
from core.base_agent import BaseAgent

logger = logging.getLogger(__name__)

class AgentSupervisor:
    def __init__(self):
        self.agents: Dict[str, BaseAgent] = {}
        self.tasks: Dict[str, asyncio.Task] = {}
        self.restart_counts: Dict[str, int] = {}
        self.max_restarts = 5
        self._shutdown_event = asyncio.Event()

    def add_agent(self, agent: BaseAgent):
        self.agents[agent.name] = agent
        self.restart_counts[agent.name] = 0

    def load_plugins(self, plugins_dir: str = "plugins", **kwargs):
        """Dynamically loads agent plugins from the specified directory."""
        if not os.path.exists(plugins_dir):
            os.makedirs(plugins_dir)
            return

        for filename in os.listdir(plugins_dir):
            if filename.endswith(".py") and not filename.startswith("__"):
                path = os.path.join(plugins_dir, filename)
                module_name = f"plugins.{filename[:-3]}"
                
                spec = importlib.util.spec_from_file_location(module_name, path)
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    
                    for name, obj in inspect.getmembers(module):
                        if inspect.isclass(obj) and issubclass(obj, BaseAgent) and obj is not BaseAgent:
                            try:
                                # Attempt to instantiate with provided kwargs that match init signature
                                sig = inspect.signature(obj.__init__)
                                agent_args = {k: v for k, v in kwargs.items() if k in sig.parameters}
                                agent_instance = obj(**agent_args)
                                self.add_agent(agent_instance)
                                logger.info(f"Supervisor: Loaded plugin agent '{agent_instance.name}' from {filename}")
                            except Exception as e:
                                logger.error(f"Supervisor: Failed to load plugin {name} from {filename}: {e}")

    async def start(self):
        logger.info("Supervisor starting agents...")
        for name, agent in self.agents.items():
            self.tasks[name] = asyncio.create_task(self._run_with_restart(agent))
        
        await self._shutdown_event.wait()

    async def _run_with_restart(self, agent: BaseAgent):
        while not self._shutdown_event.is_set():
            try:
                logger.info(f"Supervisor: Starting agent {agent.name}")
                await agent.run()
            except asyncio.CancelledError:
                logger.info(f"Supervisor: Agent {agent.name} cancelled")
                break
            except Exception as e:
                self.restart_counts[agent.name] += 1
                logger.error(f"Supervisor: Agent {agent.name} failed (Attempt {self.restart_counts[agent.name]}): {e}", exc_info=True)
                
                if self.restart_counts[agent.name] >= self.max_restarts:
                    logger.critical(f"Supervisor: Agent {agent.name} reached max restarts. Disabling.")
                    break
                
                # Exponential backoff for restarts
                delay = min(60, 1.5 ** self.restart_counts[agent.name])
                logger.info(f"Supervisor: Restarting agent {agent.name} in {delay:.2f}s")
                await asyncio.sleep(delay)

    async def stop(self):
        logger.info("Supervisor stopping all agents...")
        self._shutdown_event.set()
        for name, task in self.tasks.items():
            task.cancel()
        
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
