# Default Plugins

Core plugins that ship with LLMProxy. These use the **legacy function mode** — simple async functions that receive a `PluginContext` and modify it in-place.

All default plugins are **enabled by default** and form the security backbone of the proxy.

## Plugin Map

| Priority | Plugin | Ring | What It Does |
|----------|--------|------|-------------|
| 10 | `ingress_auth.py` | INGRESS | Zero-Trust identity enrichment (Tailscale verification) |
| 15 | `context_minifier.py` | PRE_FLIGHT | Compresses large contexts (strips comments, whitespace) |
| 20 | `pii_masker.py` | PRE_FLIGHT | Neural PII detection and masking (Presidio NLP + regex) |
| 30 | `cache_check.py` | PRE_FLIGHT | Exact-match cache lookup with tenant isolation |
| 50 | `smart_router.py` | ROUTING | EMA-weighted endpoint selection with circuit breakers + cost-aware scoring |
| 70 | `kill_switch.py` | POST_FLIGHT | Detects model infinite loops (word repetition, stuttering) |
| 80 | `shield_sanitizer.py` | POST_FLIGHT | Response sanitization (injection detection, watermarking) |
| 90 | `json_healer.py` | POST_FLIGHT | Repairs truncated JSON from streaming responses |
| 100 | `telemetry_ops.py` | BACKGROUND | Token estimation, cost tracking, audit logging |

## Writing a Default Plugin

```python
# plugins/default/my_plugin.py

from core.plugin_engine import PluginContext

async def my_function(ctx: PluginContext):
    """Ring X: What this plugin does."""
    # Access request body
    messages = ctx.body.get("messages", [])

    # Access shared state
    rotator = ctx.metadata.get("rotator")

    # Modify request
    ctx.body["messages"][-1]["content"] = "modified"

    # Or block request
    ctx.error = "Blocked by my_plugin"
    ctx.stop_chain = True
```

Then register in `manifest.yaml`:

```yaml
- name: "My Plugin"
  hook: "pre_flight"
  priority: 25
  enabled: true
  entrypoint: "default.my_plugin:my_function"
```

## Important Notes

- Default plugins have a **500ms timeout** (configurable per-plugin)
- They are AST-scanned for forbidden imports before loading
- Use `ctx.metadata` to pass data between plugins across rings
- PII masker runs at priority 20 — cache lookup at 30 sees already-masked prompts
