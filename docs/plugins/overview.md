# Plugin Engine Overview

LLMProxy features a **ring-based plugin pipeline** with 5 processing stages. The engine supports both legacy function plugins and modern `BasePlugin` class instances side by side.

## The 5 Rings

Every request flows through the rings in order:

| Ring | Stage | Purpose |
|------|-------|---------|
| 1 | **Ingress** | Auth, Zero-Trust, Global Rate Limiting |
| 2 | **Pre-Flight** | PII Masking, Prompt Mutation, Budget Guard, Loop Breaker, Cache Lookup |
| 3 | **Routing** | Dynamic Model Selection, Load Balancing, Priority Steering |
| 4 | **Post-Flight** | JSON Healing, Response Sanitization, Quality Gate, SLA Guard |
| 5 | **Background** | FinOps Tracking, Telemetry Export, Shadow Traffic |

![Plugin Pipeline](/screenshots/soc-plugins.png)

## Dual-Mode Execution

The plugin engine supports two plugin types simultaneously:

### Class Plugins (BasePlugin)

Modern plugins using the SDK. Full lifecycle hooks, typed responses, config schemas, and auto-generated SOC UI forms.

```python
class MyPlugin(BasePlugin):
    name = "my_plugin"
    hook = PluginHook.PRE_FLIGHT
    version = "1.0.0"

    async def execute(self, ctx: PluginContext) -> PluginResponse:
        return PluginResponse.passthrough()
```

### Function Plugins (Legacy)

Simple async functions — backward compatible, no breaking changes:

```python
async def my_function(ctx):
    # process request
    pass
```

The engine **auto-detects** the type: if the entrypoint is a `BasePlugin` subclass, it's instantiated; otherwise it's treated as a raw function.

## Plugin Categories

### Default Plugins (9)

Built-in function plugins, always enabled:

- Ingress Auth & Zero-Trust
- PII Neural Masker
- WAF-Aware Cache Lookup
- Enterprise Neural Router
- Post-Flight Sanitizer
- Unified Telemetry & FinOps
- Aider Context Minifier
- Speculative Kill-Switch
- JSON Auto-Healer

### Marketplace Plugins (14)

Optional `BasePlugin` class plugins, opt-in via manifest or SOC UI:

[See all marketplace plugins →](/plugins/marketplace)

### WASM Plugins

Rust/Go/C plugins compiled to WebAssembly, running in memory-safe sandbox:

[See WASM plugins →](/plugins/wasm)

## Security

All Python plugins are **AST-scanned** before loading:

- **Blocked imports**: `os`, `subprocess`, `socket`, `ctypes`, `sys`
- **Blocked calls**: `exec()`, `eval()`, `__import__()`, `.system()`, `.popen()`
- Violations raise `PluginSecurityError` — the plugin is never loaded

## Timeout Enforcement

Every plugin runs under `asyncio.wait_for(timeout)`:

- **Function plugins**: 500ms default
- **Class plugins**: 50ms default (configurable per-plugin via `timeout_ms`)
- Ingress/Routing timeouts are fatal (stop chain)
- Pre-Flight/Post-Flight timeouts are FAIL_OPEN by default

## Hot-Swap (Zero-Downtime)

Plugins can be reloaded without restart using RCU (Read-Copy-Update):

1. `on_unload()` called on existing plugins
2. Current ring state snapshotted (rollback target)
3. New plugins loaded into fresh rings
4. `on_load()` called on new plugins
5. Health check: dummy context through all rings
6. Atomic swap of active rings reference
7. Auto-rollback on any failure

```bash
curl -X POST http://localhost:8090/api/v1/plugins/hot-swap \
  -H "Authorization: Bearer your-key"
```
