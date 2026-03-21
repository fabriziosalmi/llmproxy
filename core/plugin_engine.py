"""
LLMPROXY — Plugin Engine (Dual-Mode)

Ring-based plugin pipeline with:
  - Dual-mode: raw async functions (legacy) + BasePlugin classes (marketplace)
  - AST security scanning pre-load
  - Strict per-plugin timeout enforcement (asyncio.wait_for)
  - Per-plugin metrics (invocations, errors, blocks, avg latency)
  - Zero-downtime RCU (Read-Copy-Update) hot-swap
  - WASM sandbox support via Extism (optional)
  - Plugin marketplace API
  - Health check + automatic rollback
"""

import os
import ast
import copy
import time
import importlib.util
import yaml
import asyncio
import logging
import hashlib
import inspect
from collections import deque
from enum import Enum
from typing import List, Dict, Any, Optional, Callable
from dataclasses import dataclass, field

from core.plugin_sdk import BasePlugin, PluginResponse, PluginResponseError, PluginHook as SDKHook
from core.wasm_runner import WasmRunner

class PluginHook(Enum):
    INGRESS = "ingress"         # Ring 1: Auth, ZT, Rate Limit
    PRE_FLIGHT = "pre_flight"   # Ring 2: PII, Mutation, AST
    ROUTING = "routing"         # Ring 3: Model Selection, Cache
    POST_FLIGHT = "post_flight" # Ring 4: Streaming, Sanitization, JSON Healing
    BACKGROUND = "background"   # Ring 5: FinOps, Logs, Shadow Traffic

@dataclass
class PluginState:
    """
    Shared state injected into plugins (Principle 4: Dependency Injection).

    Plugins should NOT instantiate their own connections. The proxy injects
    pre-configured, shared resources via this object.

    Attributes set by the proxy at startup:
      - cache: SemanticCache instance (or None)
      - metrics: MetricsTracker instance (or None)
      - config: Global proxy config dict
      - Any future shared resources (Redis, DB pools, etc.)
    """
    cache: Any = None          # SemanticCache instance
    metrics: Any = None        # MetricsTracker instance
    config: Dict[str, Any] = field(default_factory=dict)  # Global proxy config
    extra: Dict[str, Any] = field(default_factory=dict)    # Extensible slot

@dataclass
class PluginContext:
    request: Any = None
    body: Dict[str, Any] = field(default_factory=dict)
    response: Any = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    session_id: str = "default"
    error: Optional[str] = None
    stop_chain: bool = False
    state: Optional[PluginState] = None  # Principle 4: injected shared state

# Default timeout for raw function plugins (ms)
# 500ms is a reasonable compromise: strict enough to protect the event loop,
# permissive enough to not break legacy plugins that do light I/O.
DEFAULT_TIMEOUT_MS = 500

# Fail policies for timeout/error handling
FAIL_OPEN = "open"    # Plugin failure → request continues (default for Background/PostFlight)
FAIL_CLOSED = "closed"  # Plugin failure → request blocked (default for Ingress/Routing)

# Rings that default to fail-closed (errors stop the chain)
FAIL_CLOSED_RINGS = {PluginHook.INGRESS, PluginHook.ROUTING}

# 9.2: Forbidden AST node types for security scanning
FORBIDDEN_AST_NODES = {
    ast.Import, ast.ImportFrom,  # Checked selectively below
}
FORBIDDEN_MODULES = {
    "os", "subprocess", "shutil", "socket", "ctypes",
    "multiprocessing", "signal", "sys", "builtins", "__builtins__",
    # Blocking I/O libraries (Principle 2: Zero-Blocking Event Loop)
    "requests", "urllib",  # sync HTTP — use aiohttp/httpx instead
    "sqlite3",             # sync DB — use aiosqlite instead
}
ALLOWED_MODULES = {
    "json", "re", "math", "datetime", "hashlib", "base64",
    "typing", "dataclasses", "enum", "logging", "asyncio",
    "aiohttp", "yaml", "collections", "time",
    "core",  # core.plugin_sdk, core.plugin_engine (for PluginContext import)
}


class PluginSecurityError(Exception):
    """Raised when a plugin fails AST security scan."""
    pass


def ast_scan(source: str, plugin_name: str) -> bool:
    """
    9.2: AST scanning for plugin security validation.

    Checks for:
      - Forbidden module imports (os, subprocess, socket, etc.)
      - exec/eval calls
      - __import__ usage
      - Attribute access on forbidden globals

    Returns True if safe, raises PluginSecurityError if not.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        raise PluginSecurityError(f"Plugin '{plugin_name}': Syntax error — {e}")

    for node in ast.walk(tree):
        # Check imports
        if isinstance(node, ast.Import):
            for alias in node.names:
                module_root = alias.name.split(".")[0]
                if module_root in FORBIDDEN_MODULES:
                    raise PluginSecurityError(
                        f"Plugin '{plugin_name}': Forbidden import '{alias.name}'"
                    )
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                module_root = node.module.split(".")[0]
                if module_root in FORBIDDEN_MODULES:
                    raise PluginSecurityError(
                        f"Plugin '{plugin_name}': Forbidden import from '{node.module}'"
                    )

        # Check for exec/eval/__import__ calls
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in ("exec", "eval", "__import__", "compile"):
                raise PluginSecurityError(
                    f"Plugin '{plugin_name}': Forbidden call to '{func.id}()'"
                )
            if isinstance(func, ast.Attribute) and func.attr in ("exec", "eval", "system", "popen"):
                raise PluginSecurityError(
                    f"Plugin '{plugin_name}': Forbidden method call '.{func.attr}()'"
                )
            # Principle 2: Block time.sleep() — use asyncio.sleep() instead
            if (isinstance(func, ast.Attribute) and func.attr == "sleep"
                    and isinstance(func.value, ast.Name) and func.value.id == "time"):
                raise PluginSecurityError(
                    f"Plugin '{plugin_name}': Blocking call 'time.sleep()' — use 'await asyncio.sleep()' instead"
                )

    return True


class PluginManager:
    def __init__(self, plugins_dir: str = "plugins"):
        self.plugins_dir = plugins_dir
        self.rings: Dict[PluginHook, List[Dict[str, Any]]] = {hook: [] for hook in PluginHook}
        self._previous_rings: Optional[Dict[PluginHook, List[Dict[str, Any]]]] = None
        self.logger = logging.getLogger("plugin_engine")
        self.manifest_path = os.path.join(plugins_dir, "manifest.yaml")
        self._plugin_meta: Dict[str, Dict[str, Any]] = {}  # name → metadata for marketplace
        self._plugin_instances: Dict[str, BasePlugin] = {}  # name → BasePlugin instances
        self._plugin_stats: Dict[str, Dict[str, Any]] = {}  # name → per-plugin metrics
        # Per-plugin latency histogram: deque(maxlen=N) gives O(1) append + auto-eviction
        self._latency_window: Dict[str, deque] = {}  # name → rolling window of latency samples
        self._latency_window_size = 500
        # Per-ring timing: dict keyed by req_id for O(1) lookup during a request,
        # plus an ordered list for chronological iteration / capping at max size.
        self._ring_traces: list = []       # ordered list, capped at _ring_traces_max
        self._ring_traces_index: Dict[str, dict] = {}  # req_id → trace dict (O(1) lookup)
        self._ring_traces_max = 100

    def _init_stats(self, name: str):
        """Initialize per-plugin stats tracker."""
        if name not in self._plugin_stats:
            self._plugin_stats[name] = {
                "invocations": 0,
                "errors": 0,
                "blocks": 0,
                "timeouts": 0,
                "total_latency_ms": 0.0,
            }
        if name not in self._latency_window:
            self._latency_window[name] = deque(maxlen=self._latency_window_size)

    @staticmethod
    def _percentiles(samples: list) -> Dict[str, float]:
        """Compute P50/P95/P99 from a list of latency samples."""
        if not samples:
            return {"p50": 0, "p95": 0, "p99": 0}
        s = sorted(samples)
        n = len(s)
        return {
            "p50": round(s[int(n * 0.50)], 2),
            "p95": round(s[min(int(n * 0.95), n - 1)], 2),
            "p99": round(s[min(int(n * 0.99), n - 1)], 2),
        }

    def get_plugin_stats(self, name: str = None) -> Any:
        """Get per-plugin metrics with P50/P95/P99 latency. If name is None, return all."""
        if name:
            stats = self._plugin_stats.get(name, {})
            inv = stats.get("invocations", 0)
            return {
                **stats,
                "avg_latency_ms": round(stats["total_latency_ms"] / inv, 2) if inv > 0 else 0,
                "latency_percentiles": self._percentiles(self._latency_window.get(name, [])),
            }
        # All plugins
        result = {}
        for pname, stats in self._plugin_stats.items():
            inv = stats.get("invocations", 0)
            result[pname] = {
                **stats,
                "avg_latency_ms": round(stats["total_latency_ms"] / inv, 2) if inv > 0 else 0,
                "latency_percentiles": self._percentiles(self._latency_window.get(pname, [])),
            }
        return result

    def get_ring_traces(self, limit: int = 20) -> list:
        """Get recent ring execution traces for timeline visualization."""
        return list(reversed(self._ring_traces[-limit:]))

    def get_ring_latency(self) -> Dict[str, Any]:
        """Aggregate ring-level latency percentiles from recent traces."""
        ring_samples: Dict[str, list] = {h.value: [] for h in PluginHook}
        for trace in self._ring_traces:
            for ring_name, ring_data in trace.get("rings", {}).items():
                if ring_name in ring_samples:
                    ring_samples[ring_name].append(ring_data["duration_ms"])
        return {
            ring: {
                "count": len(samples),
                **self._percentiles(samples),
            }
            for ring, samples in ring_samples.items()
        }

    async def load_plugins(self):
        """Discovers and loads plugins based on manifest.yaml."""
        if not os.path.exists(self.manifest_path):
            self.logger.warning(f"Plugin manifest not found at {self.manifest_path}")
            return

        with open(self.manifest_path, 'r') as f:
            manifest = yaml.safe_load(f) or {}

        # Unload existing BasePlugin instances
        for name, instance in self._plugin_instances.items():
            try:
                await instance.on_unload()
            except Exception as e:
                self.logger.error(f"Error unloading plugin {name}: {e}")
        self._plugin_instances.clear()

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
        timeout_ms = p_info.get("timeout_ms", DEFAULT_TIMEOUT_MS)
        config = p_info.get("config", {})
        # Fail policy: "open" (request continues on failure) or "closed" (request blocked)
        # Default: closed for Ingress/Routing, open for everything else
        default_fail = FAIL_CLOSED if hook in FAIL_CLOSED_RINGS else FAIL_OPEN
        fail_policy = p_info.get("fail_policy", default_fail)

        # Store metadata (ui_schema, version, author) for marketplace
        self._plugin_meta[name] = {
            "name": name,
            "hook": hook.value,
            "type": p_type,
            "version": p_info.get("version", "0.0.0"),
            "author": p_info.get("author", "unknown"),
            "description": p_info.get("description", ""),
            "ui_schema": p_info.get("ui_schema"),
            "enabled": p_info.get("enabled", True),
            "timeout_ms": timeout_ms,
            "fail_policy": fail_policy,
        }

        self._init_stats(name)

        if p_type == "python":
            module_path, target_name = entrypoint.split(":")
            file_path = os.path.join(self.plugins_dir, f"{module_path.replace('.', '/')}.py")

            if not os.path.exists(file_path):
                raise FileNotFoundError(f"Plugin file not found: {file_path}")

            # AST security scan before loading
            with open(file_path, 'r') as f:
                source = f.read()
            ast_scan(source, name)

            spec = importlib.util.spec_from_file_location(name, file_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            target = getattr(module, target_name)

            # ── Dual-mode detection ──
            # If target is a class that subclasses BasePlugin → class mode
            # If target is a function → legacy raw function mode
            if inspect.isclass(target) and issubclass(target, BasePlugin):
                instance = target(config=config)
                await instance.on_load()
                self._plugin_instances[name] = instance
                self.rings[hook].append({
                    "type": "class",
                    "instance": instance,
                    "name": name,
                    "timeout_ms": instance.timeout_ms,
                    "fail_policy": fail_policy,
                })
                self.logger.info(f"Plugin Loaded (class): {name} v{instance.version} → {hook.value} [fail={fail_policy}]")
            else:
                # Legacy raw function
                func = target
                self.rings[hook].append({
                    "type": "python",
                    "func": func,
                    "name": name,
                    "timeout_ms": timeout_ms,
                    "fail_policy": fail_policy,
                })
                self.logger.info(f"Plugin Loaded (function): {name} → {hook.value} [fail={fail_policy}]")

        elif p_type == "wasm":
            file_path = os.path.join(self.plugins_dir, f"{entrypoint.replace('.', '/')}.wasm")
            if not os.path.exists(file_path):
                raise FileNotFoundError(f"WASM plugin not found: {file_path}")

            # Initialize WasmRunner with async thread-pool execution
            runner = WasmRunner(wasm_path=file_path, config=config)
            loaded = await runner.load()

            self.rings[hook].append({
                "type": "wasm",
                "path": file_path,
                "name": name,
                "timeout_ms": timeout_ms,
                "fail_policy": fail_policy,
                "_runner": runner if loaded else None,
            })
            status = "LIVE" if loaded else "STUB (extism not installed)"
            self.logger.info(f"Plugin Prepared (WASM): {name} [{status}]")

    def _should_stop_on_failure(self, plugin: Dict[str, Any], hook: PluginHook) -> bool:
        """
        Determine if a plugin failure should stop the chain.
        Uses per-plugin fail_policy, falling back to ring defaults.
        """
        policy = plugin.get("fail_policy", FAIL_CLOSED if hook in FAIL_CLOSED_RINGS else FAIL_OPEN)
        return policy == FAIL_CLOSED

    async def execute_ring(self, hook: PluginHook, context: PluginContext):
        """
        Executes all plugins in a specific ring sequentially.

        Supports three plugin types:
          - "class": BasePlugin instances (marketplace) → execute(ctx) → PluginResponse
          - "python": raw async/sync functions (legacy) → func(ctx)
          - "wasm": WASM plugins via Extism

        All plugins run under strict timeout enforcement.
        Per-plugin metrics are tracked automatically.
        Fail policy (open/closed) is per-plugin configurable (Principle 1).
        PluginResponse is validated (Principle 3).
        """
        ring_start = time.perf_counter()
        ring_plugin_timings = []

        for p in self.rings[hook]:
            if context.stop_chain:
                break

            name = p.get("name", "unknown")
            timeout_ms = p.get("timeout_ms", DEFAULT_TIMEOUT_MS)
            timeout_s = timeout_ms / 1000.0
            stats = self._plugin_stats.get(name)
            fail_closed = self._should_stop_on_failure(p, hook)

            t0 = time.perf_counter()

            try:
                if p["type"] == "class":
                    # ── BasePlugin class mode ──
                    instance: BasePlugin = p["instance"]
                    try:
                        result: PluginResponse = await asyncio.wait_for(
                            instance.execute(context), timeout=timeout_s
                        )
                    except asyncio.TimeoutError:
                        self.logger.warning(f"Plugin {name} TIMEOUT ({timeout_ms}ms) in {hook.value}")
                        if stats:
                            stats["timeouts"] += 1
                            stats["errors"] += 1
                        context.error = f"Plugin {name} timed out"
                        if fail_closed:
                            context.stop_chain = True
                        continue

                    # Principle 3: Validate PluginResponse
                    if result is None:
                        self.logger.warning(f"Plugin {name} returned None, treating as passthrough")
                        continue
                    if not isinstance(result, PluginResponse):
                        self.logger.error(
                            f"Plugin {name} returned {type(result).__name__} instead of PluginResponse — ignored"
                        )
                        if stats:
                            stats["errors"] += 1
                        if fail_closed:
                            context.error = f"Plugin {name} returned invalid response type"
                            context.stop_chain = True
                        continue

                    # Apply PluginResponse to context
                    if result.action != "passthrough":
                        if result.action == "block":
                            context.stop_chain = True
                            context.error = result.message or "Blocked by plugin"
                            context.metadata["_block_status"] = result.status_code
                            context.metadata["_block_error_type"] = result.error_type
                            if stats:
                                stats["blocks"] += 1
                        elif result.action == "modify":
                            if result.body:
                                context.body = result.body
                            if result.message:
                                context.metadata["_plugin_message"] = result.message
                        elif result.action == "cache_hit":
                            context.response = result.response
                            context.stop_chain = True
                            context.metadata["_cache_hit"] = True

                elif p["type"] == "python":
                    # ── Legacy raw function mode ──
                    func = p["func"]
                    try:
                        if inspect.iscoroutinefunction(func):
                            await asyncio.wait_for(func(context), timeout=timeout_s)
                        else:
                            # Sync functions run in executor with timeout
                            loop = asyncio.get_event_loop()
                            await asyncio.wait_for(
                                loop.run_in_executor(None, func, context),
                                timeout=timeout_s
                            )
                    except asyncio.TimeoutError:
                        self.logger.warning(f"Plugin {name} TIMEOUT ({timeout_ms}ms) in {hook.value}")
                        if stats:
                            stats["timeouts"] += 1
                            stats["errors"] += 1
                        context.error = f"Plugin {name} timed out"
                        if fail_closed:
                            context.stop_chain = True
                        continue

                elif p["type"] == "wasm":
                    await self._execute_wasm(p, context)

            except PluginResponseError as e:
                # Principle 3: Plugin returned malformed response
                self.logger.error(f"Plugin {name} invalid response: {e}")
                if stats:
                    stats["errors"] += 1
                if fail_closed:
                    context.error = str(e)
                    context.stop_chain = True

            except Exception as e:
                self.logger.error(f"Error executing plugin {name} in {hook.value}: {e}")
                context.error = str(e)
                if stats:
                    stats["errors"] += 1
                if fail_closed:
                    context.stop_chain = True
            finally:
                elapsed_ms = (time.perf_counter() - t0) * 1000
                ring_plugin_timings.append({"name": name, "ms": round(elapsed_ms, 2)})
                if stats:
                    stats["invocations"] += 1
                    stats["total_latency_ms"] += elapsed_ms
                # Rolling latency window for percentile calculation
                # deque(maxlen=N) auto-evicts oldest entry — no manual trimming needed
                window = self._latency_window.get(name)
                if window is not None:
                    window.append(elapsed_ms)

        # Record ring timing for request trace (O(1) via index dict)
        ring_elapsed_ms = (time.perf_counter() - ring_start) * 1000
        req_id = context.metadata.get("req_id", "unknown")
        if req_id != "unknown":
            trace = self._ring_traces_index.get(req_id)
            if trace is None:
                trace = {"req_id": req_id, "timestamp": time.time(), "rings": {}}
                self._ring_traces_index[req_id] = trace
                self._ring_traces.append(trace)
                # Evict oldest entry when cap is exceeded
                if len(self._ring_traces) > self._ring_traces_max:
                    evicted = self._ring_traces.pop(0)
                    self._ring_traces_index.pop(evicted["req_id"], None)
            trace["rings"][hook.value] = {
                "duration_ms": round(ring_elapsed_ms, 2),
                "plugins": ring_plugin_timings,
            }

    async def _execute_wasm(self, plugin: Dict[str, Any], context: PluginContext):
        """
        Execute a WASM plugin via WasmRunner.

        Uses asyncio.to_thread for non-blocking execution.
        Returns PluginResponse mapped to context (same logic as class plugins).
        """
        runner = plugin.get("_runner")
        if runner is None:
            self.logger.debug(f"WASM runner not initialized for {plugin['name']}")
            return

        result: PluginResponse = await runner.execute(
            body=context.body,
            metadata=context.metadata,
            session_id=context.session_id,
        )

        # Apply PluginResponse to context (same logic as class plugins)
        if result and result.action != "passthrough":
            name = plugin.get("name", "wasm_unknown")
            stats = self._plugin_stats.get(name)

            if result.action == "block":
                context.stop_chain = True
                context.error = result.message or "Blocked by WASM plugin"
                context.metadata["_block_status"] = result.status_code
                context.metadata["_block_error_type"] = result.error_type
                if stats:
                    stats["blocks"] += 1
            elif result.action == "modify":
                if result.body:
                    context.body = result.body
                if result.message:
                    context.metadata["_plugin_message"] = result.message
            elif result.action == "cache_hit":
                context.response = result.response
                context.stop_chain = True
                context.metadata["_cache_hit"] = True

    async def hot_swap(self):
        """
        9.3: Zero-downtime RCU (Read-Copy-Update) hot-swap.

        1. Snapshot current rings as rollback target
        2. Load new plugin configuration into fresh rings
        3. Atomic swap: replace active rings reference
        4. Old rings are kept for rollback
        5. Health check — rollback if any plugin fails
        """
        self.logger.info("Hot-Swap RCU initiated: loading new plugin DAG...")

        # 1. Snapshot for rollback
        old_rings = {hook: list(plugins) for hook, plugins in self.rings.items()}
        old_meta = dict(self._plugin_meta)

        try:
            # 2. Load into fresh rings
            await self.load_plugins()

            # 3. Health check — run a dummy context through all rings
            test_ctx = PluginContext(body={"_health_check": True})
            for hook in PluginHook:
                if self.rings[hook]:
                    await self.execute_ring(hook, test_ctx)
                    if test_ctx.error:
                        raise RuntimeError(f"Health check failed at ring {hook.value}: {test_ctx.error}")

            # 4. Swap succeeded — store rollback point
            self._previous_rings = old_rings
            self.logger.info("Hot-Swap RCU complete: new plugin DAG is LIVE")

        except Exception as e:
            # 5. Rollback
            self.logger.error(f"Hot-Swap RCU failed, rolling back: {e}")
            self.rings = old_rings
            self._plugin_meta = old_meta
            raise

    async def rollback(self):
        """Manually rollback to the previous plugin configuration."""
        if self._previous_rings:
            self.rings = self._previous_rings
            self._previous_rings = None
            self.logger.info("Plugin rollback executed")
        else:
            self.logger.warning("No previous plugin state to rollback to")

    # ── 9.4: Plugin Marketplace API ──

    def list_plugins(self) -> List[Dict[str, Any]]:
        """List all known plugins with metadata for the marketplace UI."""
        return list(self._plugin_meta.values())

    def get_plugin(self, name: str) -> Optional[Dict[str, Any]]:
        """Get metadata for a specific plugin."""
        return self._plugin_meta.get(name)

    async def install_plugin(self, manifest_entry: Dict[str, Any]) -> bool:
        """
        Install a new plugin by adding it to the manifest and hot-swapping.
        Returns True on success.
        """
        if not os.path.exists(self.manifest_path):
            with open(self.manifest_path, 'w') as f:
                yaml.safe_dump({"plugins": []}, f)

        with open(self.manifest_path, 'r') as f:
            manifest = yaml.safe_load(f) or {"plugins": []}

        # Check for duplicates
        existing = [p for p in manifest["plugins"] if p.get("name") == manifest_entry.get("name")]
        if existing:
            self.logger.warning(f"Plugin '{manifest_entry['name']}' already installed, updating...")
            manifest["plugins"] = [p for p in manifest["plugins"] if p.get("name") != manifest_entry["name"]]

        manifest["plugins"].append(manifest_entry)

        with open(self.manifest_path, 'w') as f:
            yaml.safe_dump(manifest, f, default_flow_style=False)

        await self.hot_swap()
        return True

    async def uninstall_plugin(self, name: str) -> bool:
        """Remove a plugin from the manifest and hot-swap."""
        if not os.path.exists(self.manifest_path):
            return False

        with open(self.manifest_path, 'r') as f:
            manifest = yaml.safe_load(f) or {"plugins": []}

        original_count = len(manifest["plugins"])
        manifest["plugins"] = [p for p in manifest["plugins"] if p.get("name") != name]

        if len(manifest["plugins"]) == original_count:
            return False

        with open(self.manifest_path, 'w') as f:
            yaml.safe_dump(manifest, f, default_flow_style=False)

        self._plugin_meta.pop(name, None)
        await self.hot_swap()
        return True
