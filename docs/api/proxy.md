# API: Model Proxy

The OpenAI-compatible endpoints: chat completions, legacy completions, embeddings
and model listing.

## Authentication

Authentication is on by default (`server.auth.enabled`). Every `/v1/` route then
requires a credential in the `Authorization` header:

- an inference key from `LLM_PROXY_API_KEYS`, or
- when `identity.enabled` is true (off by default), a provider JWT or a proxy
  session token whose role holds `proxy:use`. See the [Identity API](/api/identity).

```
Authorization: Bearer <credential>
```

Without a valid credential the answer is `401`.

## Chat Completions

```
POST /v1/chat/completions
```

The request is checked by the shield and the plugin rings, forwarded to the
provider that serves the model, and the provider's answer is returned in the
OpenAI format.

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

`model` (string) and `messages` (a list of objects, each with a string `role`) are
required. `content` is a string, a list of content parts, or null. `stream` is an
optional boolean. A body that does not fit is refused with `422`. Other fields are
not validated by the proxy and are passed on to the provider adapter.

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

The response carries the headers `X-LLMProxy-Provider` and `X-LLMProxy-Request-Id`,
and `X-LLMProxy-Cache` (`HIT` or `MISS`) when the cache lookup ran.

**Behaviour:**
- **Providers.** `proxy/adapters/registry.py` recognises 24 provider names. OpenAI,
  Anthropic, Google, Azure and Ollama have their own adapter; the other 19 use the
  OpenAI-compatible adapter. A provider is used only when an endpoint for it is
  configured (`endpoints` in `config.yaml`, or added through the
  [registry API](/api/admin#registry-endpoints)).
- **Model aliases.** A name listed under `model_aliases` is replaced by its target
  before routing. In the shipped `config.yaml`, `fast` resolves to `gpt-5.4-mini`
  and `claude` to `claude-sonnet-4-6`.
- **Model groups.** A name listed under `model_groups` resolves to one model of the
  group, chosen by the group's `strategy` (`cheapest`, `fastest`, `weighted` or
  `random`) among the providers that have an API key configured. The shipped
  `config.yaml` defines one group, `auto`, with strategy `cheapest`.
- **Fallback.** When the selected endpoint's circuit is open, the connection
  fails, or the provider answers `429` or `5xx`, the entries listed for the
  requested model under `fallback_chains` are tried in order. Other `4xx` answers
  are returned as they are. A request that was delivered and then timed out
  waiting for the answer is not sent to another provider; the caller gets `504`.
- **Streaming.** `stream: true` returns server-sent events. Streamed responses do
  not pass through response sanitisation; see the
  [security overview](/security/overview#response-sanitisation).
- **Deduplication.** With an `X-Idempotency-Key` header on a non-streaming request,
  a second request from the same credential with the same key waits for the first
  and receives its response. The response is kept for 300 seconds. The header is
  ignored on streaming requests.

## Legacy Completions

```
POST /v1/completions
```

Legacy text completion. `prompt` (a string or a list of strings; a list is joined
with newlines) becomes a single user message, the request runs through the same
pipeline as chat completions, and the answer is returned in the `text_completion`
format. `model` is required. Streaming is supported.

**Request:**
```json
{
  "model": "gpt-4o-mini",
  "prompt": "Once upon a time",
  "max_tokens": 100
}
```

**Response:**
```json
{
  "id": "chatcmpl-abc123",
  "object": "text_completion",
  "created": 1790000000,
  "model": "gpt-4o-mini",
  "choices": [
    {"text": " there was a ...", "index": 0, "logprobs": null, "finish_reason": "stop"}
  ],
  "usage": {"prompt_tokens": 4, "completion_tokens": 100, "total_tokens": 104}
}
```

## Embeddings

```
POST /v1/embeddings
```

**Request:**
```json
{
  "model": "text-embedding-3-small",
  "input": "The quick brown fox"
}
```

`model` and `input` are required. `input` is a string, a list of strings, or
pre-tokenised integers.

This route does not run the plugin rings. What it does:

- The shield inspects the input (injection scoring, trajectory, link checks) and
  refuses with `403` on a match.
- **PII masking is not applied**: the input reaches the provider as sent.
- Model aliases and groups are not resolved. The provider is derived from the
  model name, and an entry for that provider must exist under `endpoints`
  (`502` otherwise).
- Every adapter except Anthropic's forwards embeddings. A model that resolves to
  Anthropic is refused with `400`.
- A key whose quota is exhausted gets `402`.
- The request is recorded in the audit log and the spend log, and its cost is
  charged to the daily budget.

The provider's response is returned in the OpenAI embeddings format.

## Model Discovery

```
GET /v1/models
```

Returns the models listed under `endpoints.*.models` in the configuration, in the
OpenAI list format, sorted by provider and model id:

```json
{
  "object": "list",
  "data": [
    {"id": "gpt-4o", "object": "model", "created": 1790000000, "owned_by": "openai"}
  ]
}
```

The list is read from the configuration; providers are not queried. Aliases and
groups are not listed.

```
GET /v1/models/{model_id}
```

Returns one model object. A model that is not in the configuration is still
answered with `200`, with `owned_by` guessed from the name; this route does not
return `404`.

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
| 403 | `permission_error` | `forbidden` | Role lacks `proxy:use`, or the shield, a plugin or the tool policy refused the request |
| 404 | `invalid_request_error` | `not_found` | Unknown resource |
| 413 | `invalid_request_error` | `payload_too_large` | Body over the size limit |
| 422 | `invalid_request_error` | `invalid_request` | Body fails validation; `param` names the field, `detail` is the list of problems |
| 429 | `rate_limit_error` | `rate_limited` | Rate limit |
| 502 / 503 / 504 | `server_error` | `bad_gateway` / `service_unavailable` / `gateway_timeout` | Upstream failed, the proxy is stopped, or no endpoint can serve the model |

Other statuses get `type: server_error` (5xx) or `invalid_request_error`, with
`code` `internal_error` or `error`. A `503` from admission control (the proxy is
at its concurrency limit) has `code: overloaded` and a `Retry-After` header.

These responses are produced before a route runs and keep their own shape: the
firewall's `403`, `400`, `408` and `413` (`{"error": "...", "message": "..."}`);
the payload size guard's `413` (`{"error": "...", "max_bytes": N}`); and the rate
limiter's `429` (`{"error": "Rate limit exceeded", "retry_after": N}`, with a
`Retry-After` header). The control plane (`/api/v1/`) does not use this envelope;
its errors are FastAPI's default `{"detail": ...}`.

## Health & Metrics

```
GET /health
GET /ready
```

Both return the same body (overall `status`, pool stats, per-component state) and
need no credentials. `/health` is **always `200`**: the verdict is in the body's
`status` (`ok`, `degraded` or `down`), which is what pollers and the Docker
healthcheck read. `/ready` carries the same verdict as a status code, **`503` when
`status` is `down`** (the store or the HTTP session is gone) and `200` otherwise
(`degraded` still serves requests). Use `/health` for liveness and `/ready` for
readiness; the Helm chart does.

```
GET /metrics
```

Prometheus text format. While authentication is on it requires an admin
credential (permission `logs:read`). The series are named `llm_proxy_*`: request
counts and errors, a request latency histogram, token usage and estimated cost,
budget gauges, a time-to-first-token histogram for streams, per-ring latency,
endpoint pool size and circuit state, and counters for blocked injections, tool
policy decisions, authentication failures, audit writes, load shedding, stream
outcomes and plugin events. Percentiles are computed by the scraper from the
histogram buckets.
