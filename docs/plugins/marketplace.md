# Marketplace Plugins

18 `BasePlugin` classes in `plugins/marketplace/`. Two are enabled in the shipped manifest (Agentic Loop Breaker and Smart Budget Guard, the latter with a 5 USD daily budget per API key); the others are off. Enable or disable them in `plugins/manifest.yaml`.

In the tables below, **Default** is the value in the shipped manifest. The number after each name is the plugin's `priority` in its ring.

Several plugins keep their state in the memory of the process (loop history, rate windows, A/B assignments, latency samples). That state is lost on restart and is not shared between instances.

Five plugins do not do what their name says in the shipped code, because an input they read is never set or the action is not implemented. They are marked **Not wired** below, with what they do instead.

## Pre-Flight Ring

### Tool Guard (6) {#tool-guard}

Looks at the `tools` (or `functions`) array of the request and acts on the tools whose name is in `restricted_tools`: `strip` removes them from the request, `block` refuses the request with HTTP 403. It matches by name only.

**Not wired.** A caller is exempt when one of its roles is in `admin_roles`. The roles are read from `ctx.metadata["_user_roles"]`, which nothing sets, so the restriction applies to every caller.

This plugin filters what a request offers to the model. The policy on which tools a response may call is a separate feature: see [Tool Policy](/security/tool-policy).

| Config | Default | Description |
|--------|---------|-------------|
| `restricted_tools` | `["execute_bash", "drop_table", "delete_database"]` | Tool names to act on |
| `action` | `"strip"` | `strip` (remove from the request) or `block` (refuse the request) |
| `admin_roles` | `["admin"]` | Roles exempt from the restriction |

### Max Tokens Enforcer (7) {#max-tokens-enforcer}

Lowers `max_tokens` to `ceiling` when the request asks for more. It reads the `max_tokens` field only; it does not look at `max_completion_tokens`.

| Config | Default | Description |
|--------|---------|-------------|
| `ceiling` | 4096 | Upper bound on `max_tokens` |
| `inject_default` | false | Set `max_tokens` to the ceiling when the request omits it |
| `log_clamp` | true | Log a warning when a request is lowered |

### System Prompt Enforcer (8) {#system-prompt-enforcer}

Adds a system message to every request. It does nothing while `prompt` is empty, which is the shipped value.

| Config | Default | Description |
|--------|---------|-------------|
| `prompt` | `""` | The system message to add |
| `mode` | `"prepend"` | `prepend`: insert before the first system message, or first if there is none. `append`: existing system messages first, then the other messages, then the added one. `replace`: remove all system messages and put the added one first |
| `skip_if_empty` | false | Do nothing when the request has no messages. Otherwise the added message becomes the only one |

### Topic Blocklist (9) {#topic-blocklist}

Searches the text of the messages whose role is in `scan_roles` for the configured topics. Only `block` refuses the request (HTTP 400); `warn` and `log` write a log line and let it through.

| Config | Default | Description |
|--------|---------|-------------|
| `topics` | `["how to make a bomb", "how to make explosives", "csam"]` | Strings or regular expressions |
| `action` | `"block"` | `block`, `warn`, or `log` |
| `match_mode` | `"keyword"` | `keyword` (substring), `whole_word`, or `regex` |
| `case_sensitive` | false | Case-sensitive matching |
| `scan_roles` | `["user"]` | Message roles to scan |

### Smart Budget Guard (11, enabled) {#smart-budget-guard}

Estimates the cost of a request before it is forwarded and refuses it (HTTP 429) when the estimate would take the caller over the budget. The token count comes from `core/tokenizer.py` and the price from `core/pricing.py`.

- A "session" is one API key: the session id is derived from the caller's credential.
- The totals are per day. They start again when the date changes.
- The totals are estimates. Nothing corrects them with the usage the provider reports.
- The totals are saved in the proxy's store and read back on the first request after a start.
- The team total is keyed by `ctx.metadata["api_key"]`, which nothing sets, so it falls back to the session id. With the shipped values the session budget is always reached first.

| Config | Default | Description |
|--------|---------|-------------|
| `session_budget_usd` | 5.0 | Daily budget per API key |
| `team_budget_usd` | 100.0 | Second daily budget (see above) |
| `avg_output_ratio` | 0.5 | Assumed output tokens per input token |
| `warn_threshold` | 0.8 | Log a warning when the session total passes this fraction of the budget |

The manifest also lists `cost_per_1k_input` and `cost_per_1k_output`. The plugin does not read them.

### Agentic Loop Breaker (12, enabled) {#agentic-loop-breaker}

Hashes the last `hash_messages` messages of each request (role, text and tool calls, SHA-256) and keeps the hashes per session for `window_seconds`. When the same hash has already been seen `max_repeats` times in the window, the request is refused with HTTP 429 and the session's history is cleared.

| Config | Default | Description |
|--------|---------|-------------|
| `max_repeats` | 3 | Identical requests allowed in the window; the next one is refused |
| `window_seconds` | 120 | Length of the window |
| `hash_messages` | 3 | Trailing messages included in the hash |

### Per-Model Rate Limiter (13) {#per-model-rate-limiter}

Counts requests per session and model over `window_seconds` and refuses with HTTP 429 above the limit. The limit for a model comes from `model_limits` (a table built into the plugin unless the config provides one), otherwise `default_rpm`.

| Config | Default | Description |
|--------|---------|-------------|
| `default_rpm` | 60 | Requests per window for models without their own limit |
| `window_seconds` | 60 | Length of the window |

### Prompt Complexity Scorer (14) {#prompt-complexity-scorer}

Computes a score from 0 to 1 out of four signals and writes it to `ctx.metadata["_prompt_complexity"]`, with a tier (`simple` below 0.3, `moderate` below 0.7, `complex`) in `_complexity_tier`. It never refuses a request. The only reader of the score is the Model Downgrader.

| Config | Default | Description |
|--------|---------|-------------|
| `depth_weight` | 0.3 | Weight of the text length |
| `turns_weight` | 0.2 | Weight of the number of messages |
| `code_weight` | 0.25 | Weight of the code block density |
| `instruction_weight` | 0.25 | Weight of the instruction density |

### Model Downgrader (16) {#model-downgrader}

Replaces the requested model when the complexity score is below `complexity_threshold` and the model has an entry in the plugin's replacement table (`downgrade_map`, built in unless the config provides one). It does nothing unless the Prompt Complexity Scorer is also enabled.

| Config | Default | Description |
|--------|---------|-------------|
| `complexity_threshold` | 0.3 | Replace the model when the score is below this |

### Context Window Guard (18) {#context-window-guard}

Estimates the prompt's tokens and refuses the request with HTTP 413 when the estimate exceeds `safety_margin` times the model's context window. The windows come from a table built into the plugin (`model_windows` in the config replaces it). A model that is not in the table is treated as having a window of 8192 tokens.

| Config | Default | Description |
|--------|---------|-------------|
| `safety_margin` | 0.9 | Fraction of the context window above which the request is refused |

## Routing Ring

Both plugins run before the Smart Router (priority 50).

### Tenant QoS Router (44) {#tenant-qos-router}

Replaces the requested model with the one mapped to the caller's tier. An empty mapping leaves the model unchanged.

**Not wired.** The tier is derived from `ctx.metadata["_user_roles"]` and `ctx.metadata["_tenant_tier"]`, which nothing sets, so every caller gets `default_tier`. With the shipped values, enabling the plugin rewrites the model of every request to `gpt-4o-mini`.

| Config | Default | Description |
|--------|---------|-------------|
| `tier_mapping` | `{free: gpt-4o-mini, basic: gpt-4o-mini, premium: ""}` | Tier name to model (empty = keep the requested model) |
| `default_tier` | `"free"` | Tier of a caller with no role and no explicit tier |
| `force_downgrade` | true | When false the plugin does nothing |

### A/B Model Router (45) {#ab-model-router}

Acts on requests whose model is `control_model` or `variant_model` (or that name no model): it sets the model to the variant with probability `split_pct` and to the control otherwise. A request that asked for the variant is reassigned in the same way. The chosen arm is written into the request body under `_ab_meta`; no other code reads that key.

| Config | Default | Description |
|--------|---------|-------------|
| `control_model` | `"gpt-4o"` | Control model |
| `variant_model` | `"gpt-4o-mini"` | Variant model |
| `split_pct` | 0.1 | Fraction assigned to the variant |
| `sticky` | true | Keep a session on the same arm (the assignment is held in memory) |
| `experiment_id` | `"ab_test"` | Label written into `_ab_meta` |

## Post-Flight Ring

Post-flight plugins read the response body. A streamed response has none, so these plugins see streamed responses as empty.

### Response Quality Gate (75) {#response-quality-gate}

Looks for an empty answer, a very short one, refusal phrases, an apology with nothing else, and text that ends without punctuation. It writes `_quality_score`, `_quality_status` and `_quality_issues` to the context metadata. It never refuses a response, and no other code reads these keys.

| Config | Default | Description |
|--------|---------|-------------|
| `min_length` | 20 | Minimum answer length in characters |
| `refusal_threshold` | 2 | Number of refusal patterns that must match |
| `check_truncation` | true | Check for an ending without punctuation |

### Latency SLA Guard (76) {#latency-sla-guard}

Compares the request's total latency and time to first token with the configured thresholds and writes `_sla_status` to the context metadata. It never refuses a response.

**Not wired.** The timestamps are read from `ctx.metadata["_request_start_time"]` and `ctx.metadata["_ttft_time"]`, which nothing sets. The measured latency is therefore 0, the status is never `warning` or `breach`, and no samples are recorded.

| Config | Default | Description |
|--------|---------|-------------|
| `ttft_p95_ms` | 500 | Threshold for the time to first token |
| `total_p95_ms` | 3000 | Threshold for the total latency |
| `hard_limit_ms` | 10000 | Latency above which the status is `breach` |
| `window_size` | 500 | Number of samples kept |

### Canary Detector (77) {#canary-detector}

Checks whether the response repeats the request's system prompt word for word (the whole prompt, or runs of consecutive words), ignoring case. A paraphrase or a translation is not detected. On a match it writes `_canary_leak` to the context metadata; with `block_on_leak` it refuses the response with HTTP 403.

| Config | Default | Description |
|--------|---------|-------------|
| `min_leak_chars` | 50 | System prompts shorter than this are not checked |
| `similarity_threshold` | 0.6 | Fraction of the system prompt that must be found |
| `block_on_leak` | false | Refuse the response on a match |

### Schema Enforcer (78) {#schema-enforcer}

Validates a JSON answer against a JSON schema (`type`, `required`, `properties`, `items`, `enum`, `minLength`, `minimum`, `maximum`).

**Not wired.** The schema is read from `ctx.metadata["_expected_schema"]`, which nothing sets, and the answer is read from `ctx.body`, which is the request. Enabled as shipped, the plugin lets every response through.

| Config | Default | Description |
|--------|---------|-------------|
| `action` | `"warn"` | `warn` (log) or `block` (HTTP 422) |
| `max_schema_size` | 8192 | Maximum schema size in characters |

## Background Ring

### Token Counter (95) {#token-counter}

Reads `usage.prompt_tokens` and `usage.completion_tokens` from a non-streamed response, prices them with `core/pricing.py` and adds them to totals kept in memory. It writes the figures to the context metadata. It does not change the totals of the Smart Budget Guard.

The manifest lists `cost_per_1k_input` and `cost_per_1k_output` for this plugin. The plugin does not read them.

### Shadow Traffic (96) {#shadow-traffic}

**Not wired.** The plugin sends no request to the shadow model. For a sampled request it looks for a registered endpoint that matches `shadow_provider` or lists `shadow_model`; if it finds one and `store_responses` is on, it saves a record in the proxy's store: the two model names, the session id, the time, and the first 200 characters of the last message. The `latency_ms` of the record is the time the lookup took.

| Config | Default | Description |
|--------|---------|-------------|
| `shadow_model` | `""` | Model to look for. Empty disables the plugin |
| `shadow_provider` | `""` | Substring to match in the endpoint URL |
| `sample_rate` | 0.05 | Fraction of requests sampled (0.0-1.0) |
| `store_responses` | true | Save the record |
