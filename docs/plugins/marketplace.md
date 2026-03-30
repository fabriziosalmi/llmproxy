# Marketplace Plugins

18 optional plugins using the BasePlugin SDK. All are disabled by default -- enable via `manifest.yaml` or the SOC UI.

## Pre-Flight Ring

### Max Tokens Enforcer {#max-tokens-enforcer}

Clamps `max_tokens` to a hard ceiling. Clients cannot exceed it. Optional default injection when the field is absent.

| Config | Default | Description |
|--------|---------|-------------|
| `ceiling` | 4096 | Hard upper bound on max_tokens |
| `inject_default` | false | Inject ceiling when client omits max_tokens |
| `log_clamp` | true | Log warning on clamp events |

### System Prompt Enforcer {#system-prompt-enforcer}

Injects, prepends, appends, or replaces the system prompt in every request. Clients cannot bypass it.

| Config | Default | Description |
|--------|---------|-------------|
| `prompt` | `""` | The enforced system prompt |
| `mode` | `"prepend"` | `prepend`, `append`, or `replace` |
| `skip_if_empty` | false | Skip when request has no messages |

### Smart Budget Guard {#smart-budget-guard}

Per-session and per-team budget enforcement with SQLite persistence and cost estimation.

| Config | Default | Description |
|--------|---------|-------------|
| `session_budget_usd` | 5.0 | Max spend per session |
| `team_budget_usd` | 100.0 | Max spend per team/API key |
| `warn_threshold` | 0.8 | Warning at this % of budget |

### Agentic Loop Breaker {#agentic-loop-breaker}

Detects AI agents stuck in retry loops via SHA-256 prompt hashing with sliding window.

| Config | Default | Description |
|--------|---------|-------------|
| `max_repeats` | 3 | Identical prompts before blocking |
| `window_seconds` | 120 | Sliding window duration |
| `hash_messages` | 3 | Trailing messages to fingerprint |

### Per-Model Rate Limiter {#per-model-rate-limiter}

Granular rate limiting per (tenant, model) pair with sliding window counters.

| Config | Default | Description |
|--------|---------|-------------|
| `default_rpm` | 60 | Requests per minute for unlisted models |
| `window_seconds` | 60 | Sliding window duration |

### Topic Blocklist {#topic-blocklist}

Blocks requests containing forbidden topics via keyword, whole-word, or regex matching.

| Config | Default | Description |
|--------|---------|-------------|
| `topics` | `[]` | Keywords or regex patterns to block |
| `action` | `"block"` | `block`, `warn`, or `log` |
| `match_mode` | `"keyword"` | `keyword`, `whole_word`, or `regex` |
| `case_sensitive` | false | Case-sensitive matching |
| `scan_roles` | `["user"]` | Message roles to scan |

### Prompt Complexity Scorer {#prompt-complexity-scorer}

Scores prompt complexity (0-1) on 4 signals for intelligent model routing.

| Config | Default | Description |
|--------|---------|-------------|
| `depth_weight` | 0.3 | Weight for token depth signal |
| `turns_weight` | 0.2 | Weight for conversation turn count |
| `code_weight` | 0.25 | Weight for code block density |
| `instruction_weight` | 0.25 | Weight for instruction density |

### Model Downgrader {#model-downgrader}

Automatically downgrades expensive models for simple prompts (10-20x cost savings). Works with the Complexity Scorer.

| Config | Default | Description |
|--------|---------|-------------|
| `complexity_threshold` | 0.3 | Downgrade when complexity is below this score |

### Tool Guard {#tool-guard}

Strips or blocks restricted tools/functions from agentic AI requests based on user RBAC roles. Prevents tool injection attacks in autonomous agent workflows.

| Config | Default | Description |
|--------|---------|-------------|
| `restricted_tools` | `[]` | Tool/function names that require admin role |
| `action` | `"strip"` | `strip` (remove silently) or `block` (reject request) |
| `admin_roles` | `["admin"]` | Roles allowed to use restricted tools |

### Context Window Guard {#context-window-guard}

Blocks requests exceeding the target model's context window (returns clear 413 instead of cryptic upstream 400).

| Config | Default | Description |
|--------|---------|-------------|
| `safety_margin` | 0.9 | Block at this fraction of context window |

## Routing Ring

### A/B Model Router {#ab-model-router}

Routes a configurable percentage of traffic to a variant model for live A/B experimentation. Supports sticky sessions.

| Config | Default | Description |
|--------|---------|-------------|
| `control_model` | `"gpt-4o"` | Primary model |
| `variant_model` | `"gpt-4o-mini"` | Model under test |
| `split_pct` | 0.1 | Fraction routed to variant |
| `sticky` | true | Pin sessions to the same arm |
| `experiment_id` | `"ab_test"` | Tag for audit log tracking |

### Tenant QoS Router {#tenant-qos-router}

Routes requests to different models based on user/tenant tier. Free-tier users get redirected to cheaper models, premium users get the model they requested. SaaS B2B cost control.

| Config | Default | Description |
|--------|---------|-------------|
| `tier_mapping` | `{free: gpt-4o-mini, premium: ""}` | Maps tier name to target model (empty = use requested) |
| `default_tier` | `"free"` | Tier for users with no explicit mapping |
| `force_downgrade` | true | Always downgrade non-premium users |

## Post-Flight Ring

### Response Quality Gate {#response-quality-gate}

Detects empty, refused ("I cannot..."), apology-only, and truncated LLM responses.

| Config | Default | Description |
|--------|---------|-------------|
| `min_length` | 20 | Minimum response length (chars) |
| `refusal_threshold` | 2 | Refusal patterns to flag |
| `check_truncation` | true | Detect mid-sentence cutoff |

### Latency SLA Guard {#latency-sla-guard}

Measures TTFT and total latency with rolling percentiles, flags SLA violations.

| Config | Default | Description |
|--------|---------|-------------|
| `ttft_p95_ms` | 500 | TTFT P95 target |
| `total_p95_ms` | 3000 | Total latency P95 target |
| `hard_limit_ms` | 10000 | Hard SLA breach threshold |
| `window_size` | 500 | Rolling window size |

### Canary Detector {#canary-detector}

Detects system prompt leakage in responses (data exfiltration protection). Optional auto-block mode.

| Config | Default | Description |
|--------|---------|-------------|
| `min_leak_chars` | 50 | Minimum leaked characters to trigger |
| `similarity_threshold` | 0.6 | Fraction of system prompt found |
| `block_on_leak` | false | Auto-block leaked responses |

### Schema Enforcer {#schema-enforcer}

Validates LLM JSON responses against a client-provided JSON schema. Catches semantically invalid responses (missing required fields, wrong types) before they reach the client application. Supports `warn` (pass through with log) and `block` (return 422) modes.

| Config | Default | Description |
|--------|---------|-------------|
| `action` | `"warn"` | `warn` (pass through) or `block` (return 422) |
| `max_schema_size` | 8192 | Maximum schema size in bytes |

## Background Ring

### Token Counter {#token-counter}

Extracts real token counts from API responses and corrects budget heuristic estimates with actual data.

| Config | Default | Description |
|--------|---------|-------------|
| `cost_per_1k_input` | 0.003 | USD per 1K input tokens |
| `cost_per_1k_output` | 0.015 | USD per 1K output tokens |

### Shadow Traffic {#shadow-traffic}

Dark launch / A/B model comparison. After the primary response is returned to the user, asynchronously sends the same prompt to a "shadow" model for comparison. Results are stored in SQLite for SOC dashboard analysis. Enables safe model migration evaluation with real production traffic.

| Config | Default | Description |
|--------|---------|-------------|
| `shadow_model` | `""` | Model to send shadow traffic to |
| `shadow_provider` | `""` | Provider for shadow model (empty = auto-detect) |
| `sample_rate` | 0.05 | Fraction of requests to shadow (0.0-1.0) |
| `store_responses` | true | Persist comparison data to SQLite |
