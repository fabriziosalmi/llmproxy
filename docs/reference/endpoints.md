# Endpoints Reference

An endpoint is an upstream LLM provider the proxy can send requests to. The adapter registry (`proxy/adapters/registry.py`) knows 24 provider names: five with an adapter of their own (`openai`, `anthropic`, `google`, `azure`, `ollama`) and 19 served by the OpenAI-compatible adapter (18 named providers plus the generic `openai-compatible`). Endpoints can be declared in `config.yaml`, in environment variables, through the admin UI, or registered by auto-discovery.

## Endpoint sources

| Source | When to use | Where it is kept | Tag in the boot banner |
|---|---|---|---|
| `config.yaml` `endpoints:` block | Provider defaults, multi-provider routing | The file; copied into the store at startup | `[config]` |
| `.env` `LLM_PROXY_ENDPOINT_<NAME>_*` | Local/custom OpenAI-compatible hosts — no YAML edit | The environment; copied into the store at startup | `[env]` |
| `POST /api/v1/registry` (admin UI) | Additions at runtime | The store and the live configuration. An API key passed here is held in process memory only | — |
| Auto-discovery | Local or peer providers that are already running | The live configuration and the store | `[auto-discovery]` |

The four sources coexist. When an environment-declared endpoint has the same id as one in `config.yaml`, the `config.yaml` entry is kept and the environment one is skipped with a warning. Auto-discovery never replaces a configured endpoint: a service whose URL is already configured is skipped, and an id collision gets an `-auto` suffix so both entries remain.

## Routing pool

Routing reads endpoints from the store (the `endpoints` table), not from `config.yaml` directly.

At startup, each endpoint of the merged configuration that has a `base_url` is written to the store with status verified, unless a row with the same id already exists. An endpoint that names an `api_key_env` is written only when that variable is set. The same step runs when auto-discovery finds a new endpoint.

Consequences:

- A new endpoint added to `config.yaml` or `.env` enters the pool at the next start.
- Changing `base_url` or `models` of an endpoint that is already stored does not change the stored row. Delete the row with `DELETE /api/v1/registry/{id}` and restart to have it written again.
- Removing an endpoint, or its key, from the configuration does not remove its row.

`GET /v1/models`, fallback chains and `/v1/embeddings` read the endpoint map of the live configuration instead.

## Runtime probe

Use the endpoint probe to check a stored endpoint without sending an inference request:

```bash
curl -X POST http://localhost:8090/api/v1/registry/openai/probe \
  -H "Authorization: Bearer your-admin-key"
```

The probe sends `GET <endpoint url>/models` with a 3-second timeout, using the endpoint's key when the live configuration names one. On a 200 it sets the endpoint's status to verified and records the latency. On any other status, a timeout or a connection error it sets the status to discovered, which takes the endpoint out of the routing pool until a later probe succeeds or the endpoint is toggled back with `POST /api/v1/registry/{id}/toggle`.

## Env-declared endpoints

Declare an OpenAI-compatible endpoint entirely through environment variables:

```bash
LLM_PROXY_ENDPOINT_<NAME>_URL=http://host:port/v1       # required
LLM_PROXY_ENDPOINT_<NAME>_KEY=sk-...                    # optional; omit for no-auth
LLM_PROXY_ENDPOINT_<NAME>_MODELS=model-a,model-b        # optional
LLM_PROXY_ENDPOINT_<NAME>_PROVIDER=openai-compatible    # optional; default openai-compatible
```

`<NAME>` becomes the endpoint id (lowercased). Several entries can coexist. Without a `_KEY` variable the endpoint is called with no credential.

### Examples

```bash
# LM Studio on the LAN, no auth
LLM_PROXY_ENDPOINT_LMSTUDIO_URL=http://192.168.1.50:1234/v1
LLM_PROXY_ENDPOINT_LMSTUDIO_MODELS=llama-3.3-70b,qwen-2.5-coder-32b

# Remote vLLM with an API key
LLM_PROXY_ENDPOINT_VLLM_URL=https://inference.internal.example.com/v1
LLM_PROXY_ENDPOINT_VLLM_KEY=sk-internal-...
LLM_PROXY_ENDPOINT_VLLM_MODELS=mixtral-8x22b
```

## Auto-discovery

At startup, and then every `discovery.scan_interval_s` seconds (default 300), the proxy probes four services:

| Service | Default port | Probe path | Adapter |
|---|---|---|---|
| Ollama | 11434 | `GET /api/tags` | `ollama` |
| LM Studio | 1234 | `GET /v1/models` | `openai-compatible` |
| vLLM | 8000 | `GET /v1/models` | `openai-compatible` |
| LiteLLM | 4000 | `GET /v1/models` | `openai-compatible` |

A service is registered when the probe answers 200 within 1.5 seconds and lists at least one model. It is registered with base URL `http://<host>:<port>/v1`, no credential, and the models it listed.

Hosts probed:

- `127.0.0.1` — bare-metal / host-network deployments
- `host.docker.internal` — Docker Desktop (macOS/Windows) plus Linux when `extra_hosts: host.docker.internal:host-gateway` is set (provided in the shipped `docker-compose.yml`)
- Anything listed in `LLM_PROXY_DISCOVERY_PEERS`

A discovered endpoint is written to the store and stays there after the service goes away. Remove it with `DELETE /api/v1/registry/{id}`.

### `LLM_PROXY_DISCOVERY_PEERS`

Comma-separated list of remote hosts to probe. Each entry is either a bare `host` (probes all four standard ports against every signature) or `host:port` (probes only that port, still matched against every signature so a custom-port Ollama works).

```bash
LLM_PROXY_DISCOVERY_PEERS=100.98.112.23,100.66.12.82,100.108.97.78:8000,nas.lan
```

Accepts IPv4 addresses and DNS names. A host that does not resolve is skipped with a warning on each scan.

### Naming

- Local hits (loopback / host gateway) register as their bare service name (`ollama`, `lmstudio`, `vllm`, `litellm`).
- Remote peers register as `<service>-<host-with-dashes>` (e.g. `lmstudio-100-98-112-23`).
- If the preferred id is already taken, the discovered endpoint registers as `<id>-auto` (then `-auto2`, …) and both remain. The shipped `config.yaml` has an `ollama` entry at `http://localhost:11434`, so a local Ollama found by discovery registers as `ollama-auto`.

### Disabling discovery

```bash
LLM_PROXY_LOCAL_DISCOVERY=0
```

…or in `config.yaml`:

```yaml
discovery:
  local_scan: false
  # peers: ["100.98.112.23", "100.108.97.78:8000"]   # used when LLM_PROXY_DISCOVERY_PEERS is unset
```

## Supported Providers

Adapters with their own request/response translation:

| `provider` | Base URL in the shipped config | Credential sent upstream | Models in the shipped config (excerpt) |
|----------|----------|------|--------|
| `openai` | `https://api.openai.com/v1` | `Authorization: Bearer` | gpt-5.4, gpt-4.1, gpt-4o, gpt-4o-mini, o3, text-embedding-3-small, … |
| `anthropic` | `https://api.anthropic.com/v1` | `x-api-key` | claude-opus-4-6, claude-sonnet-4-6, claude-haiku-4-5-20251001, … |
| `google` | `https://generativelanguage.googleapis.com/v1beta` | `x-goog-api-key` | gemini-2.5-pro, gemini-2.5-flash, text-embedding-004, … |
| `azure` | `https://{resource}.openai.azure.com/openai/deployments/{deployment}` (placeholders to replace) | `api-key` | gpt-4o, gpt-4o-mini |
| `ollama` | `http://localhost:11434` | none | llama3.3, qwen3, phi-4, gemma3, nomic-embed-text, mxbai-embed-large |

Names served by the OpenAI-compatible adapter, with an entry in the shipped config:

| `provider` | Base URL in the shipped config | Credential sent upstream | Models in the shipped config |
|----------|----------|------|--------|
| `groq` | `https://api.groq.com/openai/v1` | `Authorization: Bearer` | llama-3.3-70b-versatile, mixtral-8x7b-32768 |
| `together` | `https://api.together.xyz/v1` | `Authorization: Bearer` | meta-llama/Llama-3.3-70B-Instruct-Turbo, mistralai/Mixtral-8x7B-Instruct-v0.1 |
| `mistral` | `https://api.mistral.ai/v1` | `Authorization: Bearer` | mistral-large-latest, mistral-small-latest, codestral-latest |
| `deepseek` | `https://api.deepseek.com/v1` | `Authorization: Bearer` | deepseek-chat, deepseek-reasoner |
| `xai` | `https://api.x.ai/v1` | `Authorization: Bearer` | grok-3, grok-3-mini |
| `perplexity` | `https://api.perplexity.ai` | `Authorization: Bearer` | sonar-pro, sonar |
| `openrouter` | `https://openrouter.ai/api/v1` | `Authorization: Bearer` | none listed (empty `models`) |
| `fireworks` | `https://api.fireworks.ai/inference/v1` | `Authorization: Bearer` | accounts/fireworks/models/llama-v3p3-70b-instruct |
| `sambanova` | `https://api.sambanova.ai/v1` | `Authorization: Bearer` | Meta-Llama-3.3-70B-Instruct |

The registry also accepts `cohere`, `huggingface`, `cloudflare`, `cerebras`, `nebius`, `hyperbolic`, `novita`, `lambdalabs` and `aimlapi` on the same adapter, and `openai-compatible` for any other OpenAI-compatible API. The shipped config has no entry for these; declare one with its `base_url`.

A cloud endpoint of the shipped config is registered only when its key variable is set (see [Routing pool](#routing-pool)).

## Configuration Examples

### OpenAI

```yaml
endpoints:
  openai:
    provider: "openai"
    base_url: "https://api.openai.com/v1"
    api_key_env: "OPENAI_API_KEY"
    models: ["gpt-4o", "gpt-4o-mini", "text-embedding-3-small"]
```

### Anthropic

```yaml
  anthropic:
    provider: "anthropic"
    base_url: "https://api.anthropic.com/v1"
    api_key_env: "ANTHROPIC_API_KEY"
    models: ["claude-sonnet-4-20250514", "claude-haiku-4-5-20251001"]
```

### Google

```yaml
  google:
    provider: "google"
    base_url: "https://generativelanguage.googleapis.com/v1beta"
    api_key_env: "GOOGLE_API_KEY"
    models: ["gemini-2.5-pro", "gemini-2.5-flash", "text-embedding-004"]
```

### Ollama (Local)

```yaml
  ollama:
    provider: "ollama"
    base_url: "http://localhost:11434"
    auth_type: "none"
    models: ["llama3.3", "qwen3", "phi-4", "nomic-embed-text"]
```

### OpenAI-Compatible (Custom)

For any provider with an OpenAI-compatible API:

```yaml
  infercom:
    provider: "openai-compatible"
    base_url: "https://api.infercom.ai/v1"
    api_key_env: "INFERCOM_API_KEY"
    models: ["MiniMax-M2.5", "DeepSeek-R1"]
```

## Format Translation

Clients speak the OpenAI chat-completions format. The adapter for the target provider translates the request and the response:

- **Anthropic**: system message moved to the top-level `system` field, `max_tokens` defaulted to 4096 when absent, tool definitions, streaming events
- **Google**: `messages` to `contents` with `parts`, `assistant` role to `model`, system message to `systemInstruction`, model name in the URL path
- **Azure**: the OpenAI body is sent to `<base_url>/chat/completions` with an `api-version` query parameter (`2024-10-21`); the deployment in the URL determines the model
- **Ollama**: the OpenAI body is sent unchanged to `<base_url>/v1/chat/completions`; any `Authorization` header is removed

Image parts (`image_url`) are translated as well:
- Anthropic: a `data:` URI becomes a `base64` source, an `http(s)` URL becomes a `url` source
- Google: a `data:` URI becomes `inlineData`, an `http(s)` URL becomes `fileData` with a MIME type taken from the file extension (JPEG when unrecognised)
