# What is LLMProxy?

LLMProxy is a self-hosted gateway between your applications and LLM providers. It
speaks the OpenAI API, so existing clients point at it unchanged, and it keeps a
record of the traffic that can be verified afterwards.

It is meant for a small or regulated team that hosts its own tooling and has to be
able to account for what went to a model: who called, which model, when, at what
cost, and what the gateway refused.

## What it does

- **Records requests in a hash chain.** Each request that reaches the pipeline is a
  row whose hash covers the row before it. The chain can be keyed, checked against a
  head recorded elsewhere, and survives retention purges and GDPR erasure because
  those are recorded in it. See the [threat model](/threat_model) for what this
  proves and what it does not.
- **Applies policy to responses.** The [tool policy](/security/tool-policy) decides
  which tools a response may call; PII is masked on the way out and restored on the
  way back.
- **Screens requests.** A byte-level firewall and a scoring shield refuse known
  attack phrasings. They are lexical; the [benchmark](/security/benchmark) says how
  much they stop.
- **Translates and routes.** Adapters for OpenAI, Anthropic, Google, Azure OpenAI
  and Ollama, and an OpenAI-compatible adapter for others. Fallback chains, a
  circuit breaker per endpoint and a daily budget limit.

## What it is not

- **Not horizontally scalable.** Budget totals, per-session scoring and control
  state live in the process. Run one instance.
- **Not a high-throughput gateway.** About 1.2k requests per second per process.
- **Not an MCP or agent gateway.**
- **Not a compliance product.** It produces evidence (an audit log, exports,
  erasure records). Whether a deployment is compliant is for the operator to
  establish.
- **Not a guarantee against prompt injection.** No filter is. The measured figures
  are published so the decision can be made on numbers.

## How a request flows

```
client
  rate limiter           off by default
  byte firewall          before authentication
  authentication         inference keys on /v1/*, admin keys on /api/v1/*
  shield                 injection scoring, per-session trajectory
  plugin rings           ingress, pre-flight (PII masking, budget), routing
  upstream provider      translation, fallback, circuit breaker
  tool policy            on the response's tool calls
  plugin rings           post-flight (sanitisation), background
client
```

## Components

| Area | Where |
|---|---|
| Request pipeline | `proxy/request_pipeline.py`, `proxy/forwarder.py` |
| Provider adapters | `proxy/adapters/` |
| Firewall, shield, PII | `core/firewall_asgi.py`, `core/security.py` |
| Tool policy | `core/tool_policy.py` |
| Audit chain | `store/audit_chain.py` |
| Stores | `store/` (SQLite, Postgres) |
| Plugins | `core/plugin_engine.py`, `plugins/` |
| Admin UI | `ui/` |

Python 3.12, FastAPI, aiohttp. SQLite by default; Postgres as the store and Redis
for shared rate-limit and circuit state are optional.
