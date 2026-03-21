# Marketplace Plugins

Optional, community-contributed plugins using the **BasePlugin class SDK**. These are more structured than default plugins, with lifecycle hooks, typed responses, config schemas, and auto-generated SOC UI forms.

Marketplace plugins are **disabled by default** — users opt-in via `manifest.yaml` or the SOC UI.

## Available Plugins

| Plugin | Ring | Version | What It Does |
|--------|------|---------|-------------|
| `smart_budget_guard.py` | PRE_FLIGHT | 1.1.0 | Per-session/team budget enforcement with SQLite persistence |
| `agentic_loop_breaker.py` | PRE_FLIGHT | 1.0.0 | Detects agentic retry loops via prompt hashing |
| `model_rate_limiter.py` | PRE_FLIGHT | 1.0.0 | Per-model rate limiting with sliding window counters |
| `prompt_complexity_scorer.py` | PRE_FLIGHT | 1.0.0 | Scores prompt complexity for intelligent routing |
| `model_downgrader.py` | PRE_FLIGHT | 1.0.0 | Downgrades expensive models for simple prompts (synergy with Complexity Scorer) |
| `context_window_guard.py` | PRE_FLIGHT | 1.0.0 | Blocks requests exceeding model context window |
| `response_quality_gate.py` | POST_FLIGHT | 1.0.0 | Detects empty/refusal/truncated responses |
| `latency_sla_guard.py` | POST_FLIGHT | 1.0.0 | Measures TTFT/total latency, flags SLA violations |
| `canary_detector.py` | POST_FLIGHT | 1.0.0 | Detects system prompt leakage (data exfiltration protection) |
| `token_counter.py` | BACKGROUND | 1.0.0 | Extracts real token counts, corrects budget estimates |

## Creating a Marketplace Plugin

### 1. Create the plugin file

```python
# plugins/marketplace/my_plugin.py

from typing import Dict, Any
from core.plugin_sdk import BasePlugin, PluginResponse, PluginHook
from core.plugin_engine import PluginContext


class MyPlugin(BasePlugin):
    name = "my_plugin"
    hook = PluginHook.PRE_FLIGHT      # Which ring
    version = "1.0.0"
    author = "your-name"
    description = "What it does"
    timeout_ms = 10                    # Max execution time (ms)

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config)
        # Read config from manifest
        self.my_setting = self.config.get("my_setting", "default")

    async def execute(self, ctx: PluginContext) -> PluginResponse:
        # Your logic here
        # Access: ctx.body, ctx.metadata, ctx.session_id, ctx.state

        if something_bad:
            return PluginResponse.block(
                status_code=429,
                error_type="my_error",
                message="Blocked: reason"
            )

        # Add metadata for downstream plugins
        ctx.metadata["_my_data"] = "value"

        return PluginResponse.passthrough()

    async def on_load(self):
        self.logger.info(f"MyPlugin loaded with setting={self.my_setting}")

    async def on_unload(self):
        self.logger.info("MyPlugin unloaded")
```

### 2. Register in manifest.yaml

```yaml
- name: "My Plugin"
  hook: "pre_flight"
  priority: 25
  enabled: false                    # Disabled by default
  type: "python"
  entrypoint: "marketplace.my_plugin:MyPlugin"
  version: "1.0.0"
  author: "your-name"
  description: "What it does"
  timeout_ms: 10
  config:
    my_setting: "default"
  ui_schema:
    - key: "my_setting"
      type: "string"
      label: "My Setting"
      description: "Controls behavior X"
      default: "default"
```

### 3. Write tests

```python
# tests/test_my_plugin.py

import pytest
from plugins.marketplace.my_plugin import MyPlugin
from core.plugin_engine import PluginContext, PluginState

@pytest.mark.asyncio
async def test_my_plugin_passthrough():
    plugin = MyPlugin(config={"my_setting": "value"})
    ctx = PluginContext(
        body={"messages": [{"role": "user", "content": "hello"}]},
        session_id="test",
        metadata={},
        state=PluginState(),
    )
    result = await plugin.execute(ctx)
    assert result.action == "passthrough"
```

## Design Guidelines

1. **Keep it fast** — marketplace plugins default to 50ms timeout. Target <10ms.
2. **Use PluginResponse** — never set `ctx.stop_chain` directly; use `PluginResponse.block()`.
3. **Config via manifest** — all tunables should be in `config` + `ui_schema` for SOC UI.
4. **No blocking I/O** — `requests`, `urllib`, `sqlite3`, `time.sleep()` are blocked by AST scanner.
5. **Fail gracefully** — PRE_FLIGHT/POST_FLIGHT rings are FAIL_OPEN by default.
6. **Use metadata** — communicate with other plugins via `ctx.metadata["_your_prefix"]`.
7. **Persist via DI** — use `ctx.state.extra["store"]` for SQLite persistence (see SmartBudgetGuard).
