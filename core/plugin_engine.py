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
        entrypoint = p_info["entrypoint"]
        p_type = p_info.get("type", "python")

        if p_type == "python":
            module_path, func_name = entrypoint.split(":")
            file_path = os.path.join(self.plugins_dir, f"{module_path.replace('.', '/')}.py")
            
            if not os.path.exists(file_path):
                raise FileNotFoundError(f"Plugin file not found: {file_path}")

            spec = importlib.util.spec_from_file_location(name, file_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            func = getattr(module, func_name)
            self.rings[hook].append({"type": "python", "func": func, "name": name})
        
        elif p_type == "wasm":
            file_path = os.path.join(self.plugins_dir, f"{entrypoint.replace('.', '/')}.wasm")
            self.rings[hook].append({"type": "wasm", "path": file_path, "name": name})
            self.logger.info(f"Plugin Prepared (WASM): {name}")

        self.logger.info(f"Plugin Loaded: {name} (Hook: {hook.value})")

    async def execute_ring(self, hook: PluginHook, context: PluginContext):
        """Executes all plugins in a specific ring sequentially."""
        for p in self.rings[hook]:
            if context.stop_chain:
                break
            try:
                if p["type"] == "python":
                    func = p["func"]
                    if asyncio.iscoroutinefunction(func):
                        await func(context)
                    else:
                        func(context)
                
                elif p["type"] == "wasm":
                    # Placeholder for Extism execution
                    # self.logger.info(f"Executing WASM Plugin: {p['name']}")
                    pass

            except Exception as e:
                self.logger.error(f"Error executing plugin {p.get('name')} in {hook.value}: {e}")
                context.error = str(e)
                # Ingress/Routing errors might be fatal
                if hook in [PluginHook.INGRESS, PluginHook.ROUTING]:
                   context.stop_chain = True

    async def hot_swap(self):
        """Hot-swaps the execution engine by re-loading the manifest."""
        self.logger.info("Hot-Swap initiated: Re-loading Plugin DAG...")
        await self.load_plugins()
