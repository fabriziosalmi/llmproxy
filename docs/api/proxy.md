# API: Model Proxy

The core proxy endpoints — OpenAI-compatible API for chat, completions, embeddings, and model discovery.

## Chat Completions

```
POST /v1/chat/completions
```

Unified inference endpoint supporting all 24 providers with automatic format translation, cross-provider fallback, and model aliases.

**Headers:**
```
Authorization: Bearer <api-key>
Content-Type: application/json
X-Idempotency-Key: <optional-dedup-key>
```

**Request:**
```json
{
  "model": "gpt-4o",
  "messages": [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "Hello!"}
  ],
  "stream": false,
  "max_tokens": 1000,
  "temperature": 0.7
}
```

**Response:**
```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "model": "gpt-4o",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "Hello! How can I help you today?"
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 20,
    "completion_tokens": 10,
    "total_tokens": 30
  }
}
```

**Features:**
- Model aliases (`fast` → `gpt-4o-mini`, `claude` → `claude-sonnet`)
- Model groups (`auto` → cheapest/fastest selection)
- Cross-provider fallback chains
- Streaming (`stream: true` returns SSE)
- Request deduplication via `X-Idempotency-Key`

## Legacy Completions

```
POST /v1/completions
```

Legacy text completion endpoint. Translates `prompt` to `messages` format internally.

**Request:**
```json
{
  "model": "gpt-4o-mini",
  "prompt": "Once upon a time",
  "max_tokens": 100
}
```

## Embeddings

```
POST /v1/embeddings
```

Embedding endpoint with PII security check. Supports OpenAI, Google, Azure, and Ollama providers.

**Request:**
```json
{
  "model": "text-embedding-3-small",
  "input": "The quick brown fox"
}
```

## Model Discovery

```
GET /v1/models
```

Returns aggregated models from all configured providers. Compatible with Cursor, OpenWebUI, and other OpenAI-compatible clients.

```
GET /v1/models/{model_id}
```

Single model info with auto-detection fallback.

## Errors

Failures on the OpenAI-compatible routes (`/v1/chat/completions`,
`/v1/completions`, `/v1/embeddings`, `/v1/models`) return the OpenAI error
envelope, plus `detail` for callers that already read it:

```json
{
  "error": {
    "message": "Unauthorized: Missing API key",
    "type": "authentication_error",
    "param": null,
    "code": "invalid_api_key"
  },
  "detail": "Unauthorized: Missing API key"
}
```

`error.message` is human-readable and may change; `error.type` and `error.code`
are the stable fields to branch on, together with the HTTP status.

| Status | `type` | `code` | Typical cause |
|-------:|--------|--------|---------------|
| 400 | `invalid_request_error` | `invalid_request` | Unsupported request, for example embeddings with an Anthropic model |
| 401 | `authentication_error` | `invalid_api_key` | Missing, empty or invalid key or token |
| 402 | `insufficient_quota` | `budget_exceeded` | The key's quota or the daily budget is exhausted |
| 403 | `permission_error` | `forbidden` | Role lacks `proxy:use`, or a security guard blocked the request |
| 404 | `invalid_request_error` | `not_found` | Unknown resource |
| 413 | `invalid_request_error` | `payload_too_large` | Body over the size limit |
| 422 | `invalid_request_error` | `invalid_request` | Body fails validation; `param` names the field, `detail` is the list of problems |
| 429 | `rate_limit_error` | `rate_limited` | Rate limit |
| 502 / 503 / 504 | `server_error` | `bad_gateway` / `service_unavailable` / `gateway_timeout` | Upstream failed, the proxy is stopped, or no endpoint can serve the model |

Other statuses get `type: server_error` (5xx) or `invalid_request_error`, with
`code` `internal_error` or `error`. `Retry-After` is passed through when set.

Two responses are produced before a route runs and keep their own shape: the
firewall's `413`/`403` body (`{"error": "...", "message": "..."}`) and the rate
limiter's `429`. The control plane (`/api/v1/`) does not use this envelope; its
errors are FastAPI's default `{"detail": ...}`.

## Health & Metrics

```
GET /health
```

Liveness/readiness probe with pool stats.

```
GET /metrics
```

Prometheus metrics: req/s, errors, latency P50/P95/P99, budget, TTFT, circuit state.
