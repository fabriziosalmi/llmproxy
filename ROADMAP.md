# LLMProxy — Roadmap vs LiteLLM

## Round 1: Core Gateway Features

| # | Feature | Status | Tests | Commit |
|---|---------|--------|-------|--------|
| 1 | `GET /v1/models` — Discovery endpoint | ✅ DONE | 7 | e3c7a18 |
| 2 | Pricing table per modello — Budget reale | ✅ DONE | 19 | 1384fcd |
| 3 | Cross-provider fallback — Il vero failover | ✅ DONE | 17 | 7abd6be |
| 4 | `POST /v1/embeddings` — RAG/vector search | ✅ DONE | 22 | 1845398 |
| 5 | Routing smart latenza/costo/success | ✅ DONE | 13 | d2df6ea |
| 6 | Multimodale — image_url translation | ✅ DONE | 22 | d2df6ea |
| 7 | Virtual keys — API key per team/progetto | | | |
| 8 | Streaming token count — Costo reale su stream | | | |
| 9 | Config hot-reload — Zero downtime | | | |
| 10 | `POST /v1/completions` — Legacy text endpoint | | | |

## Round 2: Production-Grade Features

| # | Feature | Status | Tests | Commit |
|---|---------|--------|-------|--------|
| 1 | Tiktoken accurate counting | | | |
| 2 | Reasoning models (o1/o3/o4) | | | |
| 3 | Spend analytics — FinOps dashboard | | | |
| 4 | Provider prompt caching (Anthropic/OpenAI) | | | |
| 5 | Model aliases / gruppi | | | |
| 6 | Guardrails dichiarativi (YAML) | | | |
| 7 | Observability (Langfuse/LangSmith) | | | |
| 8 | Active health probing | | | |
| 9 | Request deduplication — Idempotency key | | | |
| 10 | Audit log persistente — SOC2/GDPR | | | |
