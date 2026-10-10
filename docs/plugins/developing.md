# Developing Plugins

How to write a `BasePlugin` class, declare it in the manifest, test it and load it.

## 1. Create the Plugin File

```python
# plugins/marketplace/my_plugin.py

from typing import Any

from core.plugin_engine import PluginContext
from core.plugin_sdk import BasePlugin, PluginHook, PluginResponse


class MyPlugin(BasePlugin):
    name = "my_plugin"
    hook = PluginHook.PRE_FLIGHT
    version = "1.0.0"
    author = "your-name"
    description = "Refuses requests that contain a configured word"
    timeout_ms = 10

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self.blocked_word = self.config.get("blocked_word", "")

    async def execute(self, ctx: PluginContext) -> PluginResponse:
        # The request body
        messages = ctx.body.get("messages", [])
        text = " ".join(
            m["content"] for m in messages if isinstance(m.get("content"), str)
        )

        if self.blocked_word and self.blocked_word in text:
            return PluginResponse.block(
                status_code=400,
                error_type="blocked_word",
                message="Request refused: it contains a blocked word",
            )

        # Metadata for the plugins that run after this one
        ctx.metadata["_my_plugin_checked"] = True

        return PluginResponse.passthrough()

    async def on_load(self):
        self.logger.info(f"Loaded with blocked_word={self.blocked_word!r}")

    async def on_unload(self):
        self.logger.info("Unloaded")
```

The file must pass the loader's import lint: only the modules on the allow-list in `core/plugin_engine.py` may be imported (see [What the loader checks](/plugins/overview#what-the-loader-checks)).

## 2. Register in Manifest

Add an entry to the `plugins:` list in `plugins/manifest.yaml`:

```yaml
  - name: "My Plugin"
    hook: "pre_flight"
    priority: 25
    enabled: true
    type: "python"
    entrypoint: "marketplace.my_plugin:MyPlugin"
    version: "1.0.0"
    author: "your-name"
    description: "Refuses requests that contain a configured word"
    config:
      blocked_word: "forbidden"
    ui_schema:
      - key: "blocked_word"
        type: "text"
        label: "Blocked word"
        description: "Requests containing this word are refused"
        default: ""
```

| Key | Meaning |
|-----|---------|
| `name` | The name the API and the admin UI use for the plugin |
| `hook` | The ring: `ingress`, `pre_flight`, `routing`, `post_flight` or `background`. This decides where the plugin runs; the class attribute `hook` does not |
| `priority` | Order within the ring, ascending. 100 if absent |
| `enabled` | `true` if absent |
| `entrypoint` | `module.path:Name`, relative to `plugins/` |
| `config` | The dict passed to the plugin's constructor |
| `fail_policy` | `open` or `closed`. If absent, the ring's default applies |
| `timeout_ms` | Enforced for function and WASM plugins. For a class plugin the class attribute is enforced instead |
| `sha256` | Optional pin. When present, the file must match it |

### ui_schema

`ui_schema` is descriptive. No backend code interprets it, and the plugin does not receive its `default` values: the plugin's settings are the `config` block. The admin UI shows each field's `label` (or `key`) and `default`, read-only. The shipped manifest uses the types `text`, `number`, `boolean`, `select`, `textarea` and `array`.

## 3. Write Tests

```python
# tests/test_my_plugin.py

import pytest

from core.plugin_engine import PluginContext, PluginState
from plugins.marketplace.my_plugin import MyPlugin


def _ctx(content: str) -> PluginContext:
    return PluginContext(
        body={"messages": [{"role": "user", "content": content}]},
        session_id="test",
        metadata={},
        state=PluginState(),
    )


@pytest.mark.asyncio
async def test_passthrough():
    plugin = MyPlugin(config={"blocked_word": "forbidden"})
    ctx = _ctx("hello")
    result = await plugin.execute(ctx)
    assert result.action == "passthrough"
    assert ctx.metadata["_my_plugin_checked"] is True


@pytest.mark.asyncio
async def test_block():
    plugin = MyPlugin(config={"blocked_word": "forbidden"})
    result = await plugin.execute(_ctx("this is forbidden"))
    assert result.action == "block"
    assert result.status_code == 400
```

Run:

```bash
python -m pytest tests/test_my_plugin.py -v
```

## Design Guidelines

1. **Set a timeout.** `timeout_ms` on the class is enforced with `asyncio.wait_for`. `BasePlugin` defaults to 50 ms. A call that exceeds it counts as a failure of the plugin
2. **Return a `PluginResponse`.** Do not set `ctx.stop_chain` yourself
3. **Read settings from `config`.** The constructor receives the manifest's `config` block as `self.config`
4. **No blocking I/O.** The loader's lint refuses `requests`, `urllib`, `sqlite3` and `time.sleep()`
5. **Declare what a failure means.** Ingress, Pre-Flight and Routing are fail-closed by default (an error or timeout refuses the request); Post-Flight and Background are fail-open. Set `fail_policy` in the manifest to change it
6. **Use metadata.** Pass values to later plugins through `ctx.metadata["_your_prefix"]`
7. **Persist through the injected store.** `ctx.state.extra["store"]` is the proxy's store (SQLite or PostgreSQL, whichever the proxy is configured with)
8. **Know what the ring sees.** `ctx.body` is the request body in every ring. In Post-Flight and Background the upstream answer is `ctx.response`; a streamed response has no `body` to read

## Persistence Example

The store has `get_state(key, default=None)` and `set_state(key, value)`:

```python
async def execute(self, ctx: PluginContext) -> PluginResponse:
    store = ctx.state.extra.get("store") if ctx.state else None
    if store:
        key = f"my_plugin:{ctx.session_id}:count"
        count = int(await store.get_state(key, 0)) + 1
        await store.set_state(key, count)

    return PluginResponse.passthrough()
```

## Loading the Plugin

Restart the proxy. The manifest is read at startup.

The plugin API can also change the plugin set, with the limits below:

```bash
# Reload the manifest
curl -X POST http://localhost:8090/api/v1/plugins/hot-swap \
  -H "Authorization: Bearer your-key"

# Enable or disable a plugin of the bundled manifest, by its manifest name
curl -X POST http://localhost:8090/api/v1/plugins/toggle \
  -H "Authorization: Bearer your-key" \
  -H "Content-Type: application/json" \
  -d '{"name": "My Plugin", "enabled": true}'

# Restore the rings that were live before the last successful hot-swap
curl -X POST http://localhost:8090/api/v1/plugins/rollback \
  -H "Authorization: Bearer your-key"
```

- **Hot-swap is rolled back with the shipped manifest.** Its health check fails on the default plugins (see [Hot-swap](/plugins/overview#hot-swap)). The route still answers HTTP 200, with `{"status": "rolled_back", ...}`. `toggle` writes `plugins/manifest.yaml` and then hot-swaps: the file is changed, the running plugin set is not, and the call answers HTTP 500.
- **`toggle` does not check the name.** A name that is not in the bundled manifest changes nothing and the route still answers with the name and the flag.
- **`POST /api/v1/plugins/install`** requires `name`, `hook` and `entrypoint`. It writes the entry to `plugins/installed/manifest.yaml` and then hot-swaps. A Python plugin from that manifest is refused unless the entry also sets `"allow_inprocess": true`. The refusal comes after the entry has been written: it stays in the file, and every later load of the manifests raises the same error until the entry is removed by hand or with `DELETE /api/v1/plugins/{name}`. That call removes the entry and then answers HTTP 500, because the hot-swap that follows it fails.
- **`DELETE /api/v1/plugins/{name}`** removes entries of `plugins/installed/manifest.yaml` only. For a plugin of the bundled manifest it answers 404.
