# WASM Plugins

LLMProxy can run WebAssembly modules as plugins through the [Extism](https://extism.org/) Python SDK (`core/wasm_runner.py`).

## Status

- The `extism` package is commented out in `requirements.txt` and is not in the published image. Without it, WASM plugins are skipped.
- The shipped manifest declares no WASM plugin, and the repository tracks no compiled `.wasm` module.
- The tests of the runner use a mocked Extism. No test runs a real module.
- **In the request pipeline, a WASM plugin call fails before it reaches the module.** The runner serialises the context metadata with `json.dumps`, and the pipeline always puts the orchestrator object in that metadata (`ctx.metadata["rotator"]`), which is not JSON-serialisable. The engine records the failure as an error of the plugin: in a fail-closed ring the request is refused with `Plugin <name> failed`, in a fail-open ring the plugin is skipped. The rest of this page describes the runner's contract as written.

## What the runner does and does not do

- The module is loaded with `extism.Plugin(wasm_bytes, wasi=True)`. The runner passes Extism no allowed paths and no allowed hosts.
- The runner sets no memory limit and no instruction limit of its own.
- Calls run on a dedicated pool of 8 threads, so they do not block the event loop.
- Calls to one module are serialised by a lock: a module handles one request at a time.
- The engine waits for `timeout_ms` (500 ms if the manifest entry sets none). When the wait expires the request moves on; the call itself is not interrupted and keeps its thread and the module's lock until it returns.

## Failure handling

Once the input has been serialised, a WASM plugin does not follow the ring's fail policy. A timeout, an exception inside the Extism call, an empty result or a result that is not valid JSON are all treated as a passthrough, whatever `fail_policy` says. Timeouts are counted in the plugin's `timeouts` and `errors` statistics.

A failure to serialise the input (see Status) is the exception: it is handled like the failure of any other plugin, under the fail policy.

A WASM plugin can still refuse a request by returning a `block` action.

## Prerequisites

```bash
pip install extism
```

If the `extism` package or the `libextism` shared library cannot be loaded, the runner logs the cause as an error at load time and the plugin is skipped: the request passes through it unchanged.

## JSON I/O Protocol

The runner calls the module's exported function **`handle`** with a JSON document and reads a JSON document back.

**Input:**
```json
{
  "body": { "messages": [...] },
  "metadata": {},
  "session_id": "abc123",
  "config": { "my_setting": "value" }
}
```

`body` and `metadata` are the context's; `config` is the `config` block of the manifest entry. The input is built with `json.dumps`, so every value in the context metadata must be JSON-serialisable for the call to happen (see Status).

**Output:**
```json
{
  "action": "block",
  "status_code": 403,
  "error_type": "wasm_block",
  "message": "Refused by the plugin"
}
```

### Actions

`action` is case-insensitive. An unknown action is a passthrough.

| `action` | Fields read | Effect |
|----------|-------------|--------|
| `passthrough` or `allow` | none | The request continues |
| `modify` or `modified` | `body`, `message` | `body`, if present, replaces the request body |
| `block` | `status_code` (default 403), `error_type` (default `wasm_block`), `message` or `reason` | The chain stops with that error |
| `cache_hit` | `response` | `response` becomes the response and the chain stops |

## Writing a module

Any language with an Extism PDK can produce a module, as long as it exports `handle` and follows the protocol above. `plugins/wasm/README.md` in the repository has a Rust walkthrough; it is not built or tested by this project's CI.

## Registration

Register a WASM plugin in `plugins/manifest.yaml`. The `entrypoint` is the path of the module relative to `plugins/`, without the `.wasm` extension:

```yaml
  - name: "My WASM Plugin"
    hook: "pre_flight"
    priority: 25
    enabled: true
    type: "wasm"
    entrypoint: "wasm/my_plugin"    # plugins/wasm/my_plugin.wasm
    version: "0.1.0"
    timeout_ms: 100
    config:
      my_setting: "value"
```
