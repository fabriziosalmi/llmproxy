# Marketplace Plugins

14 optional plugins using the BasePlugin SDK. All are disabled by default — enable via `manifest.yaml` or the SOC UI.

## Pre-Flight Ring

### Max Tokens Enforcer

Clamps `max_tokens` to a hard ceiling. Clients cannot exceed it. Optional default injection when the field is absent.

| Config | Default | Description |
|--------|---------|-------------|
| `ceiling` | 4096 | Hard upper bound on max_tokens |
| `inject_default` | false | Inject ceiling when client omits max_tokens |
| `log_clamp` | true | Log warning on clamp events |

### System Prompt Enforcer

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

### Agentic Loop Breaker

Detects AI agents stuck in retry loops via SHA-256 prompt hashing with sliding window.

| Config | Default | Description |
|--------|---------|-------------|
| `max_repeats` | 3 | Identical prompts before blocking |
| `window_seconds` | 120 | Sliding window duration |
| `hash_messages` | 3 | Trailing messages to fingerprint |

### Per-Model Rate Limiter

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

### Prompt Complexity Scorer

Scores prompt complexity (0-1) on 4 signals for intelligent model routing.

| Config | Default | Description |
|--------|---------|-------------|
| `depth_weight` | 0.3 | Weight for token depth signal |
| `turns_weight` | 0.2 | Weight for conversation turn count |
| `code_weight` | 0.25 | Weight for code block density |
| `instruction_weight` | 0.25 | Weight for instruction density |

### Model Downgrader

Automatically downgrades expensive models for simple prompts (10-20x cost savings). Works with the Complexity Scorer.

| Config | Default | Description |
|--------|---------|-------------|
| `complexity_threshold` | 0.3 | Downgrade when complexity is below this score |

### Context Window Guard

Blocks requests exceeding the target model's context window (returns clear 413 instead of cryptic upstream 400).

| Config | Default | Description |
|--------|---------|-------------|
| `safety_margin` | 0.9 | Block at this fraction of context window |

## Routing Ring

### A/B Model Router

Routes a configurable percentage of traffic to a variant model for live A/B experimentation. Supports sticky sessions.

| Config | Default | Description |
|--------|---------|-------------|
| `control_model` | `"gpt-4o"` | Primary model |
| `variant_model` | `"gpt-4o-mini"` | Model under test |
| `split_pct` | 0.1 | Fraction routed to variant |
| `sticky` | true | Pin sessions to the same arm |
| `experiment_id` | `"ab_test"` | Tag for audit log tracking |

## Post-Flight Ring

### Response Quality Gate

Detects empty, refused ("I cannot..."), apology-only, and truncated LLM responses.

| Config | Default | Description |
|--------|---------|-------------|
| `min_length` | 20 | Minimum response length (chars) |
| `refusal_threshold` | 2 | Refusal patterns to flag |
| `check_truncation` | true | Detect mid-sentence cutoff |

### Latency SLA Guard

Measures TTFT and total latency with rolling percentiles, flags SLA violations.

| Config | Default | Description |
|--------|---------|-------------|
| `ttft_p95_ms` | 500 | TTFT P95 target |
| `total_p95_ms` | 3000 | Total latency P95 target |
| `hard_limit_ms` | 10000 | Hard SLA breach threshold |
| `window_size` | 500 | Rolling window size |

### Canary Detector

Detects system prompt leakage in responses (data exfiltration protection). Optional auto-block mode.

| Config | Default | Description |
|--------|---------|-------------|
| `min_leak_chars` | 50 | Minimum leaked characters to trigger |
| `similarity_threshold` | 0.6 | Fraction of system prompt found |
| `block_on_leak` | false | Auto-block leaked responses |

## Background Ring

### Token Counter

Extracts real token counts from API responses and corrects budget heuristic estimates with actual data.

| Config | Default | Description |
|--------|---------|-------------|
| `cost_per_1k_input` | 0.003 | USD per 1K input tokens |
| `cost_per_1k_output` | 0.015 | USD per 1K output tokens |
