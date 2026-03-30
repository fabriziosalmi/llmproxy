# Plugin SDK

The official SDK for building LLMProxy marketplace plugins (`core/plugin_sdk.py`).

## BasePlugin

Every marketplace plugin subclasses `BasePlugin`:

```python
from core.plugin_sdk import BasePlugin, PluginResponse, PluginHook
from core.plugin_engine import PluginContext

class MyPlugin(BasePlugin):
    name = "my_plugin"           # Unique identifier
    hook = PluginHook.PRE_FLIGHT # Which ring
    version = "1.0.0"           # Semver
    author = "your-name"
    description = "What it does"
    timeout_ms = 10              # Max execution time (ms)

    def __init__(self, config=None):
        super().__init__(config)
        self.my_setting = self.config.get("my_setting", "default")

    async def execute(self, ctx: PluginContext) -> PluginResponse:
        # Your logic here
        return PluginResponse.passthrough()

    async def on_load(self):
        """Called once when the plugin is loaded."""
        pass

    async def on_unload(self):
        """Called on hot-swap or removal."""
        pass
```

## PluginHook

The 5 rings:

```python
class PluginHook(Enum):
    INGRESS = "ingress"         # Ring 1: Auth, ZT, Rate Limit
    PRE_FLIGHT = "pre_flight"   # Ring 2: PII, Mutation, Cache
    ROUTING = "routing"         # Ring 3: Model Selection
    POST_FLIGHT = "post_flight" # Ring 4: Sanitization, Healing
    BACKGROUND = "background"   # Ring 5: FinOps, Logs, Telemetry
```

## PluginResponse

Typed return values from `execute()`:

| Factory Method | Action | Effect |
|---------------|--------|--------|
| `PluginResponse.passthrough()` | Let request continue unchanged |
| `PluginResponse.modify(body=..., message=...)` | Mutate request body, continue pipeline |
| `PluginResponse.block(status_code=403, error_type=..., message=...)` | Stop chain, return error to client |
| `PluginResponse.cache_hit(response=...)` | Return cached response, skip routing |

### Validation Rules

- `action` must be a valid `PluginAction` enum value
- `BLOCK` requires `status_code >= 400` (auto-corrected to 403 if lower)
- `MODIFY` should set `body`
- `CACHE_HIT` should set `response`

Invalid actions raise `PluginResponseError`.

## PluginContext

The context object passed to every plugin:

```python
@dataclass
class PluginContext:
    body: dict          # Request body (mutable)
    metadata: dict      # Shared metadata between plugins
    session_id: str     # Client session identifier
    state: PluginState  # Shared mutable state (DI)
```

### Metadata Convention

Plugins communicate via `ctx.metadata` using prefixed keys:

```python
# Set in your plugin
ctx.metadata["_my_plugin_score"] = 0.85

# Read from another plugin
score = ctx.metadata.get("_complexity_score", 0.0)
```

### PluginState (Dependency Injection)

Shared mutable state injected into every context:

```python
# Access SQLite store for persistence
store = ctx.state.extra.get("store")
if store:
    await store.set_state("my_key", "my_value")
    value = await store.get_state("my_key")
```

## Per-Plugin Metrics

Tracked automatically by the engine:

| Metric | Description |
|--------|-------------|
| `invocations` | Total execute() calls |
| `errors` | Unhandled exceptions |
| `blocks` | BLOCK responses returned |
| `timeouts` | Timeout kills |
| `total_latency_ms` | Cumulative execution time |
| `avg_latency_ms` | Average execution time |

Access via:

```python
stats = plugin.stats  # dict with all metrics
```
