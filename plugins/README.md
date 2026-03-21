# LLMProxy Plugin System

LLMProxy uses a **ring-based plugin pipeline** with 5 execution rings. Every request flows through the rings in order, and each plugin can inspect, modify, block, or cache the request/response.

## Architecture

```
Request → [Ring 1: INGRESS] → [Ring 2: PRE_FLIGHT] → [Ring 3: ROUTING] → [Ring 4: POST_FLIGHT] → [Ring 5: BACKGROUND] → Response
```

| Ring | Name | Purpose | Fail Policy |
|------|------|---------|-------------|
| 1 | `ingress` | Auth, Zero-Trust, rate limiting | FAIL_CLOSED |
| 2 | `pre_flight` | PII masking, cache lookup, budget, mutation | FAIL_OPEN |
| 3 | `routing` | Endpoint selection, load balancing | FAIL_CLOSED |
| 4 | `post_flight` | Response sanitization, JSON healing | FAIL_OPEN |
| 5 | `background` | Telemetry, cost tracking, async tasks | FAIL_OPEN |

**FAIL_CLOSED**: Plugin error = request blocked (critical path).
**FAIL_OPEN**: Plugin error = request continues (non-critical).

## Plugin Types

### 1. Legacy Function Plugins (`plugins/default/`)
Simple async functions. Quick to write, ideal for single-purpose logic.

```python
async def my_plugin(ctx: PluginContext):
    # Inspect/modify ctx.body, ctx.metadata, ctx.response
    pass
```

### 2. BasePlugin Class Plugins (`plugins/marketplace/`)
Full-featured plugins with lifecycle hooks, config, and typed responses.

```python
from core.plugin_sdk import BasePlugin, PluginResponse, PluginHook
from core.plugin_engine import PluginContext

class MyPlugin(BasePlugin):
    name = "my_plugin"
    hook = PluginHook.PRE_FLIGHT
    version = "1.0.0"
    timeout_ms = 10

    async def execute(self, ctx: PluginContext) -> PluginResponse:
        # Your logic here
        return PluginResponse.passthrough()
```

### 3. WASM Plugins (`plugins/wasm/`)
Memory-sandboxed plugins compiled to WebAssembly. No filesystem/network access.
See `plugins/wasm/README.md` for the Rust development guide.

## Directory Structure

```
plugins/
├── manifest.yaml          # Plugin registry — all plugins must be registered here
├── default/               # Core plugins (legacy function mode, always enabled)
├── marketplace/           # Community/optional plugins (BasePlugin class mode)
└── wasm/                  # WASM-sandboxed plugins (Rust/Go/C)
```

## Registration

Every plugin must be registered in `manifest.yaml`:

```yaml
plugins:
  # Legacy function plugin
  - name: "My Function Plugin"
    hook: "pre_flight"
    priority: 25
    enabled: true
    entrypoint: "default.my_plugin:my_function"

  # BasePlugin class plugin
  - name: "My Class Plugin"
    hook: "pre_flight"
    priority: 25
    enabled: false              # Disabled by default (opt-in)
    type: "python"
    entrypoint: "marketplace.my_plugin:MyPlugin"
    version: "1.0.0"
    author: "your-name"
    description: "What it does"
    timeout_ms: 10
    config:
      my_setting: "default_value"
    ui_schema:                  # SOC UI auto-generates config form
      - key: "my_setting"
        type: "string"
        label: "My Setting"
        description: "What this setting controls"
        default: "default_value"
```

## Priority Order

Lower number = runs first. Recommended ranges:

- **1-19**: Ingress (auth, identity)
- **20-39**: Pre-flight (PII, cache, budget, validation)
- **40-59**: Routing (endpoint selection)
- **60-89**: Post-flight (sanitization, healing, quality)
- **90-100**: Background (telemetry, export)

## PluginResponse Actions

| Action | Effect | Factory Method |
|--------|--------|---------------|
| `passthrough` | Request continues unchanged | `PluginResponse.passthrough()` |
| `modify` | Request body mutated, continues | `PluginResponse.modify(body={...})` |
| `block` | Chain stopped, error returned | `PluginResponse.block(status_code=429, message="...")` |
| `cache_hit` | Cached response returned, routing skipped | `PluginResponse.cache_hit(response)` |

## Security Constraints

All Python plugins are **AST-scanned** before loading. The following are blocked:

- **Forbidden modules**: `os`, `subprocess`, `socket`, `sys`, `multiprocessing`, `requests`, `urllib`, `sqlite3`
- **Forbidden calls**: `exec()`, `eval()`, `__import__()`, `compile()`, `time.sleep()`
- **Allowed modules**: `json`, `re`, `math`, `datetime`, `hashlib`, `base64`, `typing`, `asyncio`, `aiohttp`, `yaml`, `collections`

Use `asyncio.sleep()` instead of `time.sleep()`. Use `aiohttp` instead of `requests`.

## Shared State (PluginState DI)

Every `PluginContext` carries a `PluginState` with shared resources:

```python
ctx.state.cache     # CacheBackend instance
ctx.state.metrics   # MetricsTracker instance
ctx.state.config    # Global proxy config dict
ctx.state.extra     # Extensible dict (e.g., {"store": SqlStore})
```

## Metadata Envelope

Plugins communicate via `ctx.metadata` — a shared dict that flows across all rings:

```python
ctx.metadata["api_key"]              # Set by HTTP layer
ctx.metadata["zt_user"]              # Set by ingress_auth
ctx.metadata["pii_masked"]           # Set by pii_masker
ctx.metadata["_cache_status"]        # Set by cache_check
ctx.metadata["_estimated_cost_usd"]  # Set by smart_budget_guard
ctx.metadata["target_endpoint"]      # Set by neural_router
```

## Hot-Swap

Plugins can be added/removed at runtime without restart via the admin API.
The engine uses Read-Copy-Update (RCU) with automatic rollback on health check failure.
