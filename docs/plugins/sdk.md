# Plugin SDK

The SDK for class plugins is `core/plugin_sdk.py`. It defines `BasePlugin`, `PluginResponse`, `PluginAction`, `PluginHook` and `PluginResponseError`. The context types `PluginContext` and `PluginState` are in `core/plugin_engine.py`.

## BasePlugin

A class plugin subclasses `BasePlugin`:

```python
from core.plugin_sdk import BasePlugin, PluginResponse, PluginHook
from core.plugin_engine import PluginContext

class MyPlugin(BasePlugin):
    name = "my_plugin"           # Used for the plugin's logger name
    hook = PluginHook.PRE_FLIGHT # Informational; the manifest's hook decides the ring
    version = "1.0.0"
    author = "your-name"
    description = "What it does"
    timeout_ms = 10              # Enforced per call (ms)

    def __init__(self, config=None):
        super().__init__(config)
        self.my_setting = self.config.get("my_setting", "default")

    async def execute(self, ctx: PluginContext) -> PluginResponse:
        # Your logic here
        return PluginResponse.passthrough()

    async def on_load(self):
        """Called once after the instance is created."""
        pass

    async def on_unload(self):
        """Called when the instance is replaced by a hot-swap, and at shutdown."""
        pass
```

Class attribute defaults: `name = "unnamed_plugin"`, `hook = PluginHook.BACKGROUND`, `version = "0.0.1"`, `author = "unknown"`, `description = ""`, `timeout_ms = 50`.

The constructor receives the `config` block of the plugin's manifest entry and stores it as `self.config`. It also sets `self.logger` (`plugin.<name>`).

## PluginHook

The five rings:

```python
class PluginHook(Enum):
    INGRESS = "ingress"         # Ring 1
    PRE_FLIGHT = "pre_flight"   # Ring 2
    ROUTING = "routing"         # Ring 3
    POST_FLIGHT = "post_flight" # Ring 4
    BACKGROUND = "background"   # Ring 5
```

`core/plugin_engine.py` defines an enum with the same name and values and uses its own. The engine places a plugin in the ring named by the `hook` of its manifest entry.

## PluginResponse

The return value of `execute()`:

| Factory method | Action | Effect |
|----------------|--------|--------|
| `PluginResponse.passthrough()` | `passthrough` | The request continues unchanged |
| `PluginResponse.modify(body=None, message=None)` | `modify` | If `body` is set it replaces `ctx.body`; the pipeline continues |
| `PluginResponse.block(status_code=403, error_type="plugin_block", message="Blocked by plugin")` | `block` | The chain stops; `message` becomes the error returned to the caller |
| `PluginResponse.cache_hit(response)` | `cache_hit` | `response` becomes `ctx.response`; the chain stops |

For a `block`, the engine stores `status_code` in `ctx.metadata["_block_status"]` and `error_type` in `ctx.metadata["_block_error_type"]`. The pipeline uses the status in the Pre-Flight and Post-Flight rings; a block in the Ingress ring is answered with 403 and one in the Routing ring with 503. The pipeline does not read `_block_error_type`.

### Validation

- `action` must be one of the `PluginAction` values (`passthrough`, `modify`, `block`, `cache_hit`). Any other value raises `PluginResponseError`
- A `block` with `status_code` below 400 is changed to 403, with a warning in the log
- `modify` without a `body` and `cache_hit` without a `response` are accepted

What the engine does with other return values:

- `None` is treated as a passthrough, with a warning in the log
- Any other type counts as an error of the plugin; a fail-closed plugin refuses the request

## PluginContext

The object passed to every plugin:

```python
@dataclass
class PluginContext:
    request: Any = None
    body: dict[str, Any] = field(default_factory=dict)
    response: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)
    session_id: str = "default"
    error: str | None = None
    stop_chain: bool = False
    state: PluginState | None = None
```

| Field | Content |
|-------|---------|
| `request` | The incoming request object |
| `body` | The request body. Plugins may change it |
| `response` | The response, once there is one |
| `metadata` | A dict shared by the plugins of one request |
| `session_id` | Derived by the route from the caller's credential |
| `error`, `stop_chain` | Set by the engine when a plugin blocks or fails |
| `state` | The shared `PluginState` |

`ctx.require_rotator()` returns the orchestrator the pipeline put in `ctx.metadata["rotator"]`, or raises `RuntimeError` if it is absent.

The pipeline sets these metadata keys before the first ring: `rotator`, `req_id`, `_cache_control`, `_key_prefix`, and `_route` on the two completions routes.

### Metadata Convention

Plugins communicate via `ctx.metadata` using prefixed keys:

```python
# Set in your plugin
ctx.metadata["_my_plugin_score"] = 0.85

# Read a key another plugin set (here, the Prompt Complexity Scorer's)
score = ctx.metadata.get("_prompt_complexity", 0.0)
```

### PluginState

`ctx.state` is one object shared by all requests:

| Field | Content |
|-------|---------|
| `cache` | The proxy's cache backend |
| `metrics` | `MetricsTracker` |
| `config` | The `plugins` section of `config.yaml` |
| `extra` | `{"store": <the proxy's store>}` |

```python
store = ctx.state.extra.get("store")
if store:
    await store.set_state("my_key", "my_value")
    value = await store.get_state("my_key")
```

## Per-Plugin Metrics

The engine keeps these per plugin name:

| Metric | Description |
|--------|-------------|
| `invocations` | Calls made to the plugin |
| `errors` | Exceptions, timeouts and invalid return values |
| `blocks` | `block` responses returned |
| `timeouts` | Calls that exceeded the timeout |
| `total_latency_ms` | Cumulative execution time |
| `avg_latency_ms` | `total_latency_ms / invocations` |
| `latency_percentiles` | P50, P95 and P99 over the last 500 calls |
| `consecutive_errors`, `quarantined_until` | State of the plugin's circuit breaker |

Read them from the API:

```bash
curl http://localhost:8090/api/v1/plugins/stats \
  -H "Authorization: Bearer your-key"
```

`BasePlugin` also has a `stats` property. The engine does not update the counters behind it, so it reports zeros.
