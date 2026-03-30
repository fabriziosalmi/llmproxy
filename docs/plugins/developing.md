# Developing Plugins

Step-by-step guide to creating, testing, and publishing a marketplace plugin.

## 1. Create the Plugin File

```python
# plugins/marketplace/my_plugin.py

from typing import Dict, Any
from core.plugin_sdk import BasePlugin, PluginResponse, PluginHook
from core.plugin_engine import PluginContext


class MyPlugin(BasePlugin):
    name = "my_plugin"
    hook = PluginHook.PRE_FLIGHT
    version = "1.0.0"
    author = "your-name"
    description = "What it does"
    timeout_ms = 10

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config)
        self.my_setting = self.config.get("my_setting", "default")

    async def execute(self, ctx: PluginContext) -> PluginResponse:
        # Access request body
        messages = ctx.body.get("messages", [])

        # Your logic
        if should_block:
            return PluginResponse.block(
                status_code=429,
                error_type="my_error",
                message="Blocked: reason"
            )

        # Add metadata for downstream plugins
        ctx.metadata["_my_plugin_result"] = "value"

        return PluginResponse.passthrough()

    async def on_load(self):
        self.logger.info(f"Loaded with setting={self.my_setting}")

    async def on_unload(self):
        self.logger.info("Unloaded")
```

## 2. Register in Manifest

Add to `plugins/manifest.yaml`:

```yaml
- name: "My Plugin"
  hook: "pre_flight"
  priority: 25
  enabled: false
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
      type: "text"
      label: "My Setting"
      description: "Controls behavior X"
      default: "default"
```

### ui_schema Types

| Type | Renders As |
|------|-----------|
| `text` | Text input |
| `number` | Number input with min/max |
| `boolean` | Toggle switch |
| `select` | Dropdown (with `options` array) |
| `textarea` | Multi-line text |
| `array` | Tag list / multi-value |

## 3. Write Tests

```python
# tests/test_my_plugin.py

import pytest
from plugins.marketplace.my_plugin import MyPlugin
from core.plugin_engine import PluginContext, PluginState


@pytest.mark.asyncio
async def test_passthrough():
    plugin = MyPlugin(config={"my_setting": "value"})
    ctx = PluginContext(
        body={"messages": [{"role": "user", "content": "hello"}]},
        session_id="test",
        metadata={},
        state=PluginState(),
    )
    result = await plugin.execute(ctx)
    assert result.action == "passthrough"


@pytest.mark.asyncio
async def test_block():
    plugin = MyPlugin(config={"my_setting": "trigger"})
    ctx = PluginContext(
        body={"messages": [{"role": "user", "content": "bad input"}]},
        session_id="test",
        metadata={},
        state=PluginState(),
    )
    result = await plugin.execute(ctx)
    assert result.action == "block"
    assert result.status_code == 429
```

Run:

```bash
python -m pytest tests/test_my_plugin.py -v
```

## Design Guidelines

1. **Keep it fast** — Target <10ms execution. Marketplace plugins default to 50ms timeout
2. **Use PluginResponse** — Never set `ctx.stop_chain` directly
3. **Config via manifest** — All tunables in `config` + `ui_schema` for SOC UI
4. **No blocking I/O** — `requests`, `urllib`, `sqlite3`, `time.sleep()` are blocked by AST scanner
5. **Fail gracefully** — PRE_FLIGHT/POST_FLIGHT rings are FAIL_OPEN by default
6. **Use metadata** — Communicate with other plugins via `ctx.metadata["_your_prefix"]`
7. **Persist via DI** — Use `ctx.state.extra["store"]` for SQLite persistence

## Persistence Example

Using the injected SQLite store:

```python
async def execute(self, ctx: PluginContext) -> PluginResponse:
    store = ctx.state.extra.get("store")
    if store:
        # Read
        count = await store.get_state(f"my_plugin:{ctx.session_id}:count")
        count = int(count or 0) + 1

        # Write (fire-and-forget for performance)
        import asyncio
        asyncio.create_task(
            store.set_state(f"my_plugin:{ctx.session_id}:count", str(count))
        )

    return PluginResponse.passthrough()
```

## Hot-Swap Deployment

After creating your plugin:

```bash
# Install via API
curl -X POST http://localhost:8090/api/v1/plugins/install \
  -H "Authorization: Bearer your-key" \
  -d '{"name": "my_plugin"}'

# Hot-swap reload (zero downtime)
curl -X POST http://localhost:8090/api/v1/plugins/hot-swap \
  -H "Authorization: Bearer your-key"

# Toggle on/off
curl -X POST http://localhost:8090/api/v1/plugins/toggle \
  -H "Authorization: Bearer your-key" \
  -d '{"name": "my_plugin", "enabled": true}'

# Rollback if issues
curl -X POST http://localhost:8090/api/v1/plugins/rollback \
  -H "Authorization: Bearer your-key"
```
