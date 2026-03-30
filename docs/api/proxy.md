# API: Model Proxy

The core proxy endpoints — OpenAI-compatible API for chat, completions, embeddings, and model discovery.

## Chat Completions

```
POST /v1/chat/completions
```

Unified inference endpoint supporting all 15 providers with automatic format translation, cross-provider fallback, and model aliases.

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

## Health & Metrics

```
GET /health
```

Liveness/readiness probe with pool stats.

```
GET /metrics
```

Prometheus metrics: req/s, errors, latency P50/P95/P99, budget, TTFT, circuit state.
