# Plugin Engine Overview

LLMProxy features a **ring-based plugin pipeline** with 5 processing stages. The engine supports both legacy function plugins and modern `BasePlugin` class instances side by side.

## The 5 Rings

Every request flows through the rings in order:

| Ring | Stage | Purpose |
|------|-------|---------|
| 1 | **Ingress** | Identity enrichment, rate limiting |
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

- Ingress Auth & Zero-Trust (attaches the caller's identity; it does not deny)
- PII Neural Masker (regular expressions, or Presidio when installed)
- WAF-Aware Cache Lookup
- Enterprise Neural Router (endpoint selection by success rate, latency and price)
- Post-Flight Sanitizer
- Unified Telemetry & FinOps
- Aider Context Minifier
- Speculative Kill-Switch
- JSON Auto-Healer

### Marketplace Plugins

18 `BasePlugin` class plugins. Two are enabled in the shipped manifest (Agentic Loop Breaker, Smart Budget Guard); the rest are opt-in via the manifest or the admin UI:

[See all marketplace plugins →](/plugins/marketplace)

### WASM Plugins

Rust/Go/C plugins compiled to WebAssembly and run through Extism. The `extism` package is not part of the published image, so WASM plugins are skipped there unless you add it:

[See WASM plugins →](/plugins/wasm)

## What the loader checks

Python plugins run inside the proxy's process with its privileges. They are **not sandboxed**. Before loading, the source is scanned (AST) as a check against mistakes; it is not a security boundary and is easy to get around on purpose:

- **Blocked imports**: `os`, `subprocess`, `socket`, `ctypes`, `sys`
- **Blocked calls**: `exec()`, `eval()`, `__import__()`, `.system()`, `.popen()`, `time.sleep()`
- Violations raise `PluginSecurityError` — the plugin is never loaded

> [!WARNING]
> **Removed: Legacy Sync Plugins**
> Legacy synchronous plugins (function plugins without `async def`) have been permanently removed to eliminate the Thread Exhaustion Risk. All Python plugins must now be `async`. Do not install Python plugins you have not read. WASM plugins have no access to the host filesystem or network; the runner sets no memory or instruction limit of its own.

## Timeout Enforcement

Every plugin runs under `asyncio.wait_for(timeout)`:

- **Function plugins**: 500ms default
- **Class plugins**: 50ms default (configurable per-plugin via `timeout_ms`)
- A timeout or error in the Ingress, Pre-Flight or Routing ring refuses the request (fail-closed) unless the plugin sets `fail_policy: open`
- In the Post-Flight and Background rings it lets the request through (fail-open) unless the plugin sets `fail_policy: closed`

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
