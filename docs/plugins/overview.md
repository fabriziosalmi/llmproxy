# Plugin Engine Overview

LLMProxy runs plugins in five rings (`core/plugin_engine.py`). A plugin is either an async function or a `BasePlugin` class. Both kinds are declared in `plugins/manifest.yaml` and can run side by side.

## The 5 Rings

A request passes through the rings in order. Within a ring, plugins run in ascending `priority`. The table lists the plugins that are enabled in the shipped manifest.

| Ring | Hook | Enabled in the shipped manifest (priority) |
|------|------|--------------------------------------------|
| 1 | `ingress` | Ingress Auth & Zero-Trust (10) |
| 2 | `pre_flight` | Smart Budget Guard (11), Agentic Loop Breaker (12), Aider Context Minifier (15), PII Neural Masker (20), WAF-Aware Cache Lookup (30) |
| 3 | `routing` | Smart Router (50) |
| 4 | `post_flight` | Speculative Kill-Switch (70), Post-Flight Sanitizer (80), JSON Auto-Healer (90) |
| 5 | `background` | Unified Telemetry & FinOps (100) |

The Background ring is started as a separate task once the response is ready; the request does not wait for it (`proxy/request_pipeline.py`).

What the caller receives when a ring stops the request:

| Ring | Response |
|------|----------|
| Ingress | HTTP 403 |
| Pre-Flight | the status the plugin asked for (403 if it set none), or the cached response on a cache hit |
| Routing | HTTP 503 |
| Post-Flight | the status the plugin asked for (403 if it set none) |

## Two kinds of plugin

### Class plugins (BasePlugin)

A subclass of `BasePlugin` from `core/plugin_sdk.py`. It has `on_load()` and `on_unload()` hooks and returns a `PluginResponse`.

```python
from core.plugin_sdk import BasePlugin, PluginResponse, PluginHook
from core.plugin_engine import PluginContext


class MyPlugin(BasePlugin):
    name = "my_plugin"
    hook = PluginHook.PRE_FLIGHT
    version = "1.0.0"

    async def execute(self, ctx: PluginContext) -> PluginResponse:
        return PluginResponse.passthrough()
```

The ring a plugin runs in is the `hook` of its manifest entry. The engine does not read the `hook` class attribute.

### Function plugins

An async function that receives the context and changes it in place:

```python
async def my_function(ctx):
    # read or change ctx.body, ctx.metadata, ctx.response
    pass
```

The engine looks at the entrypoint: a `BasePlugin` subclass is instantiated, anything else must be an `async def` function. A synchronous function is refused at load time; the error is logged and that plugin is skipped.

## Plugin categories

The shipped manifest declares 30 plugins.

### Default plugins (9)

Async functions in `plugins/default/`. All nine are enabled in the shipped manifest:

- Ingress Auth & Zero-Trust (attaches the caller's Tailscale identity to the context; it does not deny)
- PII Neural Masker (regular expressions, or Presidio when installed)
- WAF-Aware Cache Lookup
- Smart Router (endpoint selection by success rate, latency and price)
- Post-Flight Sanitizer
- Unified Telemetry & FinOps
- Aider Context Minifier
- Speculative Kill-Switch
- JSON Auto-Healer

### Marketplace plugins (18)

18 `BasePlugin` classes in `plugins/marketplace/`. Two are enabled in the shipped manifest (Agentic Loop Breaker, Smart Budget Guard). The other 16 are disabled. Enable one by setting `enabled: true` in the manifest, or with `POST /api/v1/plugins/toggle`. The admin UI lists only the plugins that are loaded, so a disabled plugin cannot be enabled from it.

[See all marketplace plugins →](/plugins/marketplace)

### Other bundled plugins (3)

Three `BasePlugin` classes in `plugins/installed/`, all disabled in the shipped manifest: ONNX PII Masker, l0 Compressor, AI Dependency Guard.

### WASM plugins

Modules compiled to WebAssembly and run through Extism. The `extism` package is not part of the published image, so WASM plugins are skipped there unless you add it. Read the status notes on the WASM page before relying on them:

[See WASM plugins →](/plugins/wasm)

## What the loader checks

Python plugins run inside the proxy's process with its privileges. They are **not sandboxed**. Before loading, the source is scanned (AST) as a check against mistakes; it is not a security boundary and is easy to get around on purpose:

- **Blocked imports**: `os`, `subprocess`, `shutil`, `socket`, `ctypes`, `multiprocessing`, `signal`, `sys`, `builtins`, `requests`, `urllib`, `sqlite3`
- **Allowed imports**: only the modules in `ALLOWED_MODULES` (`json`, `re`, `math`, `datetime`, `hashlib`, `base64`, `typing`, `dataclasses`, `enum`, `logging`, `asyncio`, `aiohttp`, `yaml`, `collections`, `time`, `core`, `fastapi`, `onnxruntime`, `numpy`, `transformers`, `huggingface_hub`). Any other import is refused
- **Blocked calls**: `exec()`, `eval()`, `compile()`, `__import__()`, `.exec()`, `.eval()`, `.system()`, `.popen()`, `time.sleep()`
- A violation raises `PluginSecurityError`. The engine does not catch it per plugin: it propagates out of the manifest load

Two further checks apply to Python plugins:

- **SHA-256 pin.** If the manifest entry has a `sha256`, the file must match it. A bundled entry without a pin is loaded with a warning. An entry in `plugins/installed/manifest.yaml` without a pin is refused.
- **Installed manifest.** A Python plugin declared in `plugins/installed/manifest.yaml` (where `POST /api/v1/plugins/install` writes) is refused unless its entry sets `allow_inprocess: true`.

> [!WARNING]
> Do not install Python plugins you have not read. WASM plugins have no access to the host filesystem or network; the runner sets no memory or instruction limit of its own.

## Timeout enforcement

Every plugin call runs under `asyncio.wait_for`:

- **Function and WASM plugins**: `timeout_ms` from the manifest entry, 500 ms if absent
- **Class plugins**: the `timeout_ms` class attribute (50 ms in `BasePlugin`). The manifest's `timeout_ms` is reported by the API for a class plugin but is not the value enforced
- A timeout or error in the Ingress, Pre-Flight or Routing ring refuses the request (fail-closed) unless the plugin sets `fail_policy: open`
- In the Post-Flight and Background rings it lets the request through (fail-open) unless the plugin sets `fail_policy: closed`

When a plugin raises, the caller is told `Plugin <name> failed`. The exception text goes to the log only.

A fail-open plugin is quarantined after 10 consecutive errors and skipped for 60 seconds. A fail-closed plugin is never quarantined: it runs on every request, and its failure refuses that request only.

## Hot-swap

`hot_swap()` reloads the manifest without a restart:

1. The new plugin set is built separately from the live one: each enabled plugin is loaded and `on_load()` is called on class plugins
2. The live rings, metadata, instances and statistics are replaced by the new ones in a single step with no `await` in between
3. Health check: every non-empty ring is run once with a context whose body is `{"_health_check": true}`
4. If a ring reports an error, the previous state is restored and `on_unload()` is called on the new instances
5. Otherwise `on_unload()` is called on the replaced instances, and the previous rings are kept for `rollback`

Per-plugin statistics start again from zero after a hot-swap. `rollback` restores the previous rings only.

```bash
curl -X POST http://localhost:8090/api/v1/plugins/hot-swap \
  -H "Authorization: Bearer your-key"
```

The route answers HTTP 200 in both cases: `{"status": "success", ...}` or `{"status": "rolled_back", "error": "Plugin DAG reload failed"}`.

> [!WARNING]
> With the shipped manifest the health check fails and every hot-swap is rolled back. The health-check context does not carry the orchestrator, and seven of the default plugins require it (`ctx.require_rotator()`); the Ingress Auth plugin is fail-closed, so its error fails the check. `POST /api/v1/plugins/toggle` and `POST /api/v1/plugins/install` write the manifest and then call `hot_swap()`, so the file changes but the running plugin set does not. A restart loads the manifest without the health check; so does the config watcher when `config.yaml` changes on disk (`proxy/background.py`, 30-second poll).
