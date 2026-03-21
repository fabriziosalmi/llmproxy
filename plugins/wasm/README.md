# LLMPROXY — WASM Plugin Development Guide

Build plugins in Rust (or any language that compiles to WASM) for maximum
performance and memory-safe sandboxing.

## Quick Start (Rust)

### 1. Create the project

```bash
cargo new --lib my_wasm_plugin
cd my_wasm_plugin
```

### 2. Configure Cargo.toml

```toml
[package]
name = "my_wasm_plugin"
version = "0.1.0"
edition = "2021"

[lib]
crate-type = ["cdylib"]  # Required for WASM

[dependencies]
extism-pdk = "1"
serde = { version = "1", features = ["derive"] }
serde_json = "1"
```

### 3. Implement the plugin (src/lib.rs)

```rust
use extism_pdk::*;
use serde::{Deserialize, Serialize};

/// Input from LLMPROXY (JSON)
#[derive(Deserialize)]
struct PluginInput {
    body: serde_json::Value,      // Request body (messages, model, etc.)
    metadata: serde_json::Value,  // Request metadata
    session_id: String,
    config: serde_json::Value,    // Plugin config from manifest
}

/// Output to LLMPROXY (JSON) — must match PluginResponse contract
#[derive(Serialize)]
struct PluginOutput {
    action: String,                          // "passthrough" | "modify" | "block" | "cache_hit"
    #[serde(skip_serializing_if = "Option::is_none")]
    body: Option<serde_json::Value>,         // Modified body (for "modify")
    #[serde(skip_serializing_if = "Option::is_none")]
    status_code: Option<u16>,                // HTTP status (for "block")
    #[serde(skip_serializing_if = "Option::is_none")]
    error_type: Option<String>,              // Error type (for "block")
    #[serde(skip_serializing_if = "Option::is_none")]
    message: Option<String>,                 // Human-readable message
}

/// The exported function that LLMPROXY calls
#[plugin_fn]
pub fn handle(input_json: String) -> FnResult<String> {
    let input: PluginInput = serde_json::from_str(&input_json)?;

    // Your logic here...
    // Example: pass through unchanged
    let output = PluginOutput {
        action: "passthrough".to_string(),
        body: None,
        status_code: None,
        error_type: None,
        message: None,
    };

    Ok(serde_json::to_string(&output)?)
}
```

### 4. Build

```bash
# Install WASM target (one-time)
rustup target add wasm32-unknown-unknown

# Build
cargo build --target wasm32-unknown-unknown --release

# Output: target/wasm32-unknown-unknown/release/my_wasm_plugin.wasm
```

### 5. Install in LLMPROXY

Copy the `.wasm` file to `plugins/wasm/` and add to `manifest.yaml`:

```yaml
- name: "My WASM Plugin"
  hook: "pre_flight"
  priority: 15
  enabled: true
  type: "wasm"
  entrypoint: "wasm/my_wasm_plugin"
  timeout_ms: 10
  config:
    my_setting: "value"
```

## JSON Protocol

### Input (Python → WASM)

```json
{
  "body": {
    "model": "gpt-4",
    "messages": [{"role": "user", "content": "Hello"}]
  },
  "metadata": {"api_key": "sk-...", "zt_user": "user@company.com"},
  "session_id": "abc123",
  "config": {"my_setting": "value"}
}
```

### Output (WASM → Python)

**Passthrough** (no changes):
```json
{"action": "passthrough"}
```

**Modify** (mutate request body):
```json
{
  "action": "modify",
  "body": {"model": "gpt-4", "messages": [{"role": "user", "content": "[REDACTED]"}]}
}
```

**Block** (reject request):
```json
{
  "action": "block",
  "status_code": 403,
  "error_type": "injection_detected",
  "message": "Prompt injection attempt blocked"
}
```

## Performance Notes

- WASM execution is delegated to `asyncio.to_thread()` — never blocks the event loop.
- Plugin is loaded once at startup, reused for all requests (no per-request compilation).
- JSON serialization is the main overhead (~0.1ms for typical payloads).
- The WASM sandbox prevents access to host filesystem, network, and memory.

## Requirements

Python side: `pip install extism`
Rust side: `rustup target add wasm32-unknown-unknown`
