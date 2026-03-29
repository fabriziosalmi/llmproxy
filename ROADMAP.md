# LLMProxy — Roadmap vs LiteLLM

## Round 1: Core Gateway Features

| # | Feature | Status | Tests |
|---|---------|--------|-------|
| 1 | `GET /v1/models` — Discovery endpoint | ✅ DONE | 7 |
| 2 | Pricing table per modello — Budget reale | ✅ DONE | 19 |
| 3 | Cross-provider fallback — Il vero failover | ✅ DONE | 17 |
| 4 | `POST /v1/embeddings` — RAG/vector search | ✅ DONE | 22 |
| 5 | Routing smart latenza/costo/success | ✅ DONE | 13 |
| 6 | Multimodale — image_url translation | ✅ DONE | 22 |
| 7 | Virtual keys — API key per team/progetto | SKIPPED | — |
| 8 | Streaming token count — Costo reale su stream | ✅ DONE | — |
| 9 | Config hot-reload — Zero downtime | ✅ DONE | — |
| 10 | `POST /v1/completions` — Legacy text endpoint | ✅ DONE | 4 |

## Round 2: Production-Grade Features

| # | Feature | Status | Tests |
|---|---------|--------|-------|
| 1 | Tiktoken accurate counting | ✅ DONE | 11 |
| 2 | Reasoning models (o1/o3/o4) | ✅ DONE | 13 |
| 3 | Spend analytics — FinOps dashboard | ✅ DONE | — |
| 4 | Provider prompt caching (Anthropic/OpenAI) | ✅ DONE | — |
| 5 | Model aliases / gruppi | ✅ DONE | 8 |
| 6 | Guardrails dichiarativi (YAML) | SKIPPED | — |
| 7 | Observability (Langfuse/LangSmith) | SKIPPED | — |
| 8 | Active health probing | ✅ DONE | — |
| 9 | Request deduplication — Idempotency key | ✅ DONE | 6 |
| 10 | Audit log persistente — SOC2/GDPR | ✅ DONE | — |

## Bug Fixes

| Bug | Severity | Fix |
|-----|----------|-----|
| embeddings.py forwarded proxy key to upstream | SECURITY | Provider key from api_key_env |
| deduplicator.py get_event_loop() crash | CRASH | get_running_loop() |
| export.py get_event_loop() crash | CRASH | get_running_loop() |
| completions.py streaming not translated | FUNCTIONAL | SSE chat→text_completion translation |
| health_prober.py localhost hardcoded skip | QUALITY | probe_local config flag |
| model_resolver.py fragile import | RESILIENCE | try/except graceful degrade |

## Score: 18/20 done, 2 skipped (Virtual keys, Observability callbacks)

### Guardrails — Topic Blocklist (v1.2.2)
Implemented as a marketplace plugin (`TopicBlocklist`) rather than a separate YAML system.
Supports keyword, whole_word, and regex match modes; block/warn/log actions; per-role scanning;
multimodal content; configurable via SOC UI through `ui_schema`.

870 tests, all passing, CI green.
