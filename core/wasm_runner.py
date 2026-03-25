"""
LLMPROXY — WASM Plugin Runner

Executes WebAssembly plugins via Extism SDK with proper async delegation.

Architecture:
  - WASM plugins run in a memory-sandboxed VM (no access to host filesystem/network)
  - Execution is delegated to a thread pool via asyncio.to_thread() to avoid
    blocking the event loop (WASM FFI calls are synchronous from Python's POV)
  - JSON I/O protocol aligned with PluginResponse contracts
  - Timeout enforcement at the caller level (plugin_engine.execute_ring)

JSON Protocol (WASM ↔ Python):
  Input (Python → WASM):
    {
      "body": { ... request body ... },
      "metadata": { ... request metadata ... },
      "session_id": "...",
      "config": { ... plugin config from manifest ... }
    }

  Output (WASM → Python):
    {
      "action": "passthrough" | "modify" | "block" | "cache_hit",
      "body": { ... modified body (for "modify") ... },
      "status_code": 403,             // for "block"
      "error_type": "waf_block",      // for "block"
      "message": "Injection detected" // for "block"
    }

  If WASM returns invalid/unparseable JSON → treated as passthrough (fail-open).

Usage:
  runner = WasmRunner("plugins/wasm/my_plugin.wasm", config={...})
  await runner.load()
  result = await runner.execute(context)
  await runner.unload()
"""

import json
import asyncio
import logging
import concurrent.futures
from typing import Dict, Any, Optional

from core.plugin_sdk import PluginResponse, PluginAction

logger = logging.getLogger("wasm_runner")

# Flag: is extism available?
_extism_available = None

# Dedicated thread pool for WASM execution -- prevents starvation of the
# default executor used by other asyncio.to_thread() calls.
_WASM_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=8, thread_name_prefix="wasm"
)


def _check_extism() -> bool:
    """Lazy-check if extism is installed. Cached after first call."""
    global _extism_available
    if _extism_available is None:
        try:
            import extism  # noqa: F401
            _extism_available = True
        except ImportError:
            _extism_available = False
    return _extism_available


class WasmRunner:
    """
    Executes a single WASM plugin file with JSON I/O protocol.

    Thread-safe: all WASM calls are delegated to a dedicated thread pool,
    releasing the GIL and keeping the event loop free.
    """

    def __init__(self, wasm_path: str, config: Dict[str, Any] | None = None):
        self.wasm_path = wasm_path
        self.config = config or {}
        self._plugin = None  # Extism Plugin instance
        self._loaded = False
        self.logger = logging.getLogger(f"wasm.{wasm_path.split('/')[-1]}")

    async def load(self) -> bool:
        """
        Load the WASM module into memory. Returns False if extism not installed.
        Called once at plugin load time (not per-request).
        """
        if not _check_extism():
            self.logger.warning("Extism not installed — WASM plugins unavailable. pip install extism")
            return False

        try:
            # Load in thread to avoid blocking during file I/O + compilation
            loop = asyncio.get_event_loop()
            plugin: Any = await loop.run_in_executor(_WASM_EXECUTOR, self._sync_load)  # type: ignore[func-returns-value]
            self._plugin = plugin
            self._loaded = True
            self.logger.info(f"WASM plugin loaded: {self.wasm_path}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to load WASM plugin {self.wasm_path}: {e}")
            return False

    def _sync_load(self) -> Any:
        """Synchronous WASM load (runs in thread pool)."""
        import extism
        with open(self.wasm_path, "rb") as f:
            wasm_bytes = f.read()
        return extism.Plugin(wasm_bytes, wasi=True)

    async def execute(self, body: Dict[str, Any], metadata: Dict[str, Any] | None = None,
                      session_id: str = "default") -> PluginResponse:
        """
        Execute the WASM plugin with the given context.

        Serializes context to JSON, calls the WASM "handle" function in a
        thread pool, deserializes the result into a PluginResponse.

        If anything goes wrong → returns PluginResponse.passthrough() (fail-open).
        """
        if not self._loaded or self._plugin is None:
            return PluginResponse.passthrough()

        # Build JSON input for WASM
        input_data = {
            "body": body,
            "metadata": metadata or {},
            "session_id": session_id,
            "config": self.config,
        }
        input_json = json.dumps(input_data)

        try:
            # Execute in thread pool — releases GIL, doesn't block event loop
            loop = asyncio.get_event_loop()
            result_bytes = await loop.run_in_executor(
                _WASM_EXECUTOR, self._sync_call, input_json
            )
            return self._parse_result(result_bytes)

        except Exception as e:
            self.logger.error(f"WASM execution error: {e}")
            return PluginResponse.passthrough()

    def _sync_call(self, input_json: str) -> Optional[bytes]:
        """Synchronous WASM call (runs in thread pool)."""
        if self._plugin is None:
            raise RuntimeError("WASM plugin not loaded — call load() first")
        return self._plugin.call("handle", input_json.encode("utf-8"))

    def _parse_result(self, result_bytes: Optional[bytes]) -> PluginResponse:
        """
        Parse WASM output bytes into a PluginResponse.
        If output is invalid/missing → passthrough.
        """
        if not result_bytes:
            return PluginResponse.passthrough()

        try:
            output = json.loads(result_bytes.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            self.logger.warning(f"WASM returned invalid JSON: {e}")
            return PluginResponse.passthrough()

        # Map WASM output to PluginResponse
        action = output.get("action", "passthrough").lower()

        # Normalize legacy WASM actions (ALLOW/BLOCK/MODIFIED → our format)
        action_map = {
            "allow": "passthrough",
            "modified": "modify",
            "block": "block",
            "passthrough": "passthrough",
            "modify": "modify",
            "cache_hit": "cache_hit",
        }
        action = action_map.get(action, "passthrough")

        # Validate action
        valid_actions = {a.value for a in PluginAction}
        if action not in valid_actions:
            self.logger.warning(f"WASM returned unknown action '{action}', treating as passthrough")
            return PluginResponse.passthrough()

        if action == "block":
            return PluginResponse.block(
                status_code=output.get("status_code", 403),
                error_type=output.get("error_type", "wasm_block"),
                message=output.get("message") or output.get("reason") or "Blocked by WASM plugin",
            )
        elif action == "modify":
            body = output.get("body")
            # Support legacy "clean_prompt" field from Gemini spec
            if not body and "clean_prompt" in output:
                body = {"_wasm_clean_prompt": output["clean_prompt"]}
            return PluginResponse.modify(
                body=body,
                message=output.get("message"),
            )
        elif action == "cache_hit":
            return PluginResponse.cache_hit(response=output.get("response"))
        else:
            return PluginResponse.passthrough()

    async def unload(self):
        """Release the WASM plugin from memory."""
        if self._plugin is not None:
            try:
                # Extism plugins may need explicit cleanup
                del self._plugin
            except Exception as e:
                self.logger.warning(f"WASM plugin cleanup error: {e}")
            self._plugin = None
            self._loaded = False
            self.logger.info(f"WASM plugin unloaded: {self.wasm_path}")
