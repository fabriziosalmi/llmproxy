# WASM Plugins

LLMProxy supports WebAssembly plugins via the [Extism](https://extism.org/) SDK, enabling Rust, Go, and C plugins to run in a memory-safe sandbox.

## Why WASM?

- **Memory-safe sandboxing**: WASM plugins run in an isolated VM — crashes cannot affect the Python process
- **Language freedom**: Write plugins in Rust, Go, C, or any language that compiles to WASM
- **Same guarantees**: Timeout enforcement, per-plugin metrics, and fail policies apply identically to WASM and Python plugins

## Prerequisites

```bash
pip install extism
```

If Extism is not installed, WASM plugins are skipped silently (no crash).

## JSON I/O Protocol

WASM plugins communicate via JSON:

**Input:**
```json
{
  "body": { "messages": [...] },
  "metadata": {},
  "session_id": "abc123",
  "config": { "my_setting": "value" }
}
```

**Output:**
```json
{
  "action": "ALLOW",
  "body": { "messages": [...] },
  "status_code": 200,
  "message": "Processed"
}
```

### Actions

| WASM Action | Maps To | Effect |
|-------------|---------|--------|
| `ALLOW` | `passthrough` | Let request continue |
| `BLOCK` | `block` | Stop chain, return error |
| `MODIFIED` | `modify` | Body was mutated, continue |

## Rust Plugin Template

```rust
// lib.rs
use extism_pdk::*;
use serde::{Deserialize, Serialize};

#[derive(Deserialize)]
struct PluginInput {
    body: serde_json::Value,
    metadata: serde_json::Value,
    session_id: String,
    config: serde_json::Value,
}

#[derive(Serialize)]
struct PluginOutput {
    action: String,
    body: serde_json::Value,
    status_code: u32,
    message: String,
}

#[plugin_fn]
pub fn execute(input: String) -> FnResult<String> {
    let ctx: PluginInput = serde_json::from_str(&input)?;

    let output = PluginOutput {
        action: "ALLOW".to_string(),
        body: ctx.body,
        status_code: 200,
        message: "Processed by WASM plugin".to_string(),
    };

    Ok(serde_json::to_string(&output)?)
}
```

### Cargo.toml

```toml
[package]
name = "my-wasm-plugin"
version = "0.1.0"
edition = "2021"

[lib]
crate-type = ["cdylib"]

[dependencies]
extism-pdk = "1"
serde = { version = "1", features = ["derive"] }
serde_json = "1"
```

### Build

```bash
cargo build --release --target wasm32-wasi
# Output: target/wasm32-wasi/release/my_wasm_plugin.wasm
```

## Execution Model

- All WASM calls run via `asyncio.to_thread()`, releasing the GIL
- The event loop stays free for other requests
- Timeout enforcement applies identically to Python plugins
- Non-blocking by design

## Registration

Register WASM plugins in `manifest.yaml`:

```yaml
- name: "My WASM Plugin"
  hook: "pre_flight"
  priority: 25
  enabled: true
  type: "wasm"
  entrypoint: "plugins/wasm/my_plugin.wasm"
  version: "0.1.0"
  timeout_ms: 100
  config:
    my_setting: "value"
```
