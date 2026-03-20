import os
import importlib.util
import yaml
import asyncio
import logging
from enum import Enum
from typing import List, Dict, Any, Optional, Callable
from dataclasses import dataclass, field

class PluginHook(Enum):
    INGRESS = "ingress"         # Ring 1: Auth, ZT, Rate Limit
    PRE_FLIGHT = "pre_flight"   # Ring 2: PII, Mutation, AST
    ROUTING = "routing"         # Ring 3: Model Selection, Cache
    POST_FLIGHT = "post_flight" # Ring 4: Streaming, Sanitization, JSON Healing
    BACKGROUND = "background"   # Ring 5: FinOps, Logs, Shadow Traffic

@dataclass
class PluginContext:
    request: Any = None
    body: Dict[str, Any] = field(default_factory=dict)
    response: Any = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    session_id: str = "default"
    error: Optional[str] = None
    stop_chain: bool = False

class PluginManager:
    def __init__(self, plugins_dir: str = "plugins"):
        self.plugins_dir = plugins_dir
        self.rings: Dict[PluginHook, List[Callable]] = {hook: [] for hook in PluginHook}
        self.logger = logging.getLogger("plugin_engine")
        self.manifest_path = os.path.join(plugins_dir, "manifest.yaml")

    async def load_plugins(self):
        """Discovers and loads plugins based on manifest.yaml."""
        if not os.path.exists(self.manifest_path):
            self.logger.warning(f"Plugin manifest not found at {self.manifest_path}")
            return

        with open(self.manifest_path, 'r') as f:
            manifest = yaml.safe_load(f) or {}

        # Reset rings
        for hook in PluginHook:
            self.rings[hook] = []

        plugins = manifest.get("plugins", [])
        # Sort by priority
        plugins.sort(key=lambda p: p.get("priority", 100))

        for p_info in plugins:
            if not p_info.get("enabled", True):
                continue
            
            try:
                await self._load_plugin(p_info)
            except Exception as e:
                self.logger.error(f"Failed to load plugin {p_info.get('name')}: {e}")

    async def _load_plugin(self, p_info: Dict[str, Any]):
        name = p_info["name"]
        hook = PluginHook(p_info["hook"])
        entrypoint = p_info["entrypoint"] # e.g. "default.pii_masking:analyze"
        
        module_path, func_name = entrypoint.split(":")
        file_path = os.path.join(self.plugins_dir, f"{module_path.replace('.', '/')}.py")
        
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Plugin file not found: {file_path}")

        spec = importlib.util.spec_from_file_location(name, file_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        
        func = getattr(module, func_name)
        self.rings[hook].append(func)
        self.logger.info(f"Plugin Loaded: {name} (Hook: {hook.value}, Priority: {p_info.get('priority')})")

    async def execute_ring(self, hook: PluginHook, context: PluginContext):
        """Executes all plugins in a specific ring sequentially."""
        for plugin_func in self.rings[hook]:
            if context.stop_chain:
                break
            try:
                # Plugins can be async or sync
                if asyncio.iscoroutinefunction(plugin_func):
                    await plugin_func(context)
                else:
                    plugin_func(context)
            except Exception as e:
                self.logger.error(f"Error executing plugin in {hook.value}: {e}")
                context.error = str(e)
                # Ingress/Routing errors might be fatal
                if hook in [PluginHook.INGRESS, PluginHook.ROUTING]:
                   context.stop_chain = True

    async def hot_swap(self):
        """Hot-swaps the execution engine by re-loading the manifest."""
        self.logger.info("Hot-Swap initiated: Re-loading Plugin DAG...")
        await self.load_plugins()
