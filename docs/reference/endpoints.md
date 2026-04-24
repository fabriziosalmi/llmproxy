# Endpoints Reference

LLMProxy supports 15 LLM providers with automatic request/response translation. Endpoints can be declared through `config.yaml`, `.env` variables, the admin UI, or registered automatically via local/Tailscale auto-discovery.

## Endpoint sources

| Source | When to use | Persistence | Visible in UI tag |
|---|---|---|---|
| `config.yaml` `endpoints:` block | Production defaults, multi-provider routing | Versioned | `[config]` |
| `.env` `LLM_PROXY_ENDPOINT_<NAME>_*` | Local/custom OpenAI-compatible hosts — no YAML edit | `.env` | `[env]` |
| `POST /api/v1/registry` (admin UI) | Ad-hoc additions at runtime | `endpoints.db` + live config | `[ui]` |
| Auto-discovery | Zero-config onboarding for local/Tailscale providers | In-memory only (re-probed at boot) | `[auto-discovery]` |

The four sources coexist — auto-discovery never clobbers an explicitly configured endpoint (collisions get a `-auto` or `-<host>` suffix so both entries remain visible).

## Env-declared endpoints

Declare an OpenAI-compatible endpoint entirely through environment variables:

```bash
LLM_PROXY_ENDPOINT_<NAME>_URL=http://host:port/v1       # required
LLM_PROXY_ENDPOINT_<NAME>_KEY=sk-...                    # optional; omit for no-auth
LLM_PROXY_ENDPOINT_<NAME>_MODELS=model-a,model-b        # optional
LLM_PROXY_ENDPOINT_<NAME>_PROVIDER=openai-compatible    # optional; default openai-compatible
```

`<NAME>` becomes the endpoint id (lowercased). Several entries can coexist.

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

At boot the proxy probes four well-known OpenAI-compatible services:

| Service | Default port | Probe path | Adapter |
|---|---|---|---|
| Ollama | 11434 | `GET /api/tags` | `ollama` |
| LM Studio | 1234 | `GET /v1/models` | `openai-compatible` |
| vLLM | 8000 | `GET /v1/models` | `openai-compatible` |
| LiteLLM | 4000 | `GET /v1/models` | `openai-compatible` |

Hosts probed:

- `127.0.0.1` — bare-metal / host-network deployments
- `host.docker.internal` — Docker Desktop (macOS/Windows) plus Linux when `extra_hosts: host.docker.internal:host-gateway` is set (provided in the shipped `docker-compose.yml`)
- Anything listed in `LLM_PROXY_DISCOVERY_PEERS`

### `LLM_PROXY_DISCOVERY_PEERS`

Comma-separated list of remote hosts to probe. Each entry is either a bare `host` (probes all four standard ports against every signature) or `host:port` (probes only that port, still matched against every signature so a custom-port Ollama works).

```bash
LLM_PROXY_DISCOVERY_PEERS=100.98.112.23,100.66.12.82,100.108.97.78:8000,nas.lan
```

Accepts IPs, DNS names, and Tailscale addresses. Unresolvable hosts get a single warning and are skipped.

### Naming

- Local hits (loopback / host gateway) register as their bare provider name (`ollama`, `lmstudio`, `vllm`, `litellm`).
- Remote peers register as `<provider>-<host-with-dashes>` (e.g. `lmstudio-100-98-112-23`), so multiple nodes never collide.
- If the preferred id is already taken (e.g. `config.yaml` ships an `ollama` entry pointing at a stale `localhost:11434`), the discovered endpoint registers as `<provider>-auto` and both remain visible — the operator decides which one wins.

### Disabling discovery

```bash
LLM_PROXY_LOCAL_DISCOVERY=0
```

…or in `config.yaml`:

```yaml
discovery:
  local_scan: false
  # peers: ["100.98.112.23", "100.108.97.78:8000"]   # equivalent to env var
```

## Supported Providers

| Provider | Base URL | Auth | Models |
|----------|----------|------|--------|
| **OpenAI** | `api.openai.com/v1` | Bearer | gpt-4o, gpt-4o-mini, gpt-4.1, o3-mini, embeddings |
| **Anthropic** | `api.anthropic.com/v1` | x-api-key | claude-sonnet-4, claude-haiku-4.5, claude-opus-4 |
| **Google** | `generativelanguage.googleapis.com` | API key | gemini-2.5-pro, gemini-2.5-flash, embeddings |
| **Azure** | `{resource}.openai.azure.com` | api-key | gpt-4o, gpt-4o-mini |
| **Ollama** | `localhost:11434` | None | llama3.3, qwen3, phi-4, gemma3, embeddings |
| **Groq** | `api.groq.com/openai/v1` | Bearer | llama-3.3-70b, mixtral-8x7b |
| **Together** | `api.together.xyz/v1` | Bearer | Llama-3.3-70B, Mixtral-8x7B |
| **Mistral** | `api.mistral.ai/v1` | Bearer | mistral-large, mistral-small, codestral |
| **DeepSeek** | `api.deepseek.com/v1` | Bearer | deepseek-chat, deepseek-reasoner |
| **xAI** | `api.x.ai/v1` | Bearer | grok-3, grok-3-mini |
| **Perplexity** | `api.perplexity.ai` | Bearer | sonar-pro, sonar |
| **OpenRouter** | `openrouter.ai/api/v1` | Bearer | All models via unified API |
| **Fireworks** | `api.fireworks.ai/inference/v1` | Bearer | llama-v3p3-70b-instruct |
| **SambaNova** | `api.sambanova.ai/v1` | Bearer | Meta-Llama-3.3-70B-Instruct |
| **OpenAI-Compatible** | Custom | Bearer | Any OpenAI-compatible API |

## Configuration Examples

### OpenAI

```yaml
endpoints:
  openai:
    provider: "openai"
    base_url: "https://api.openai.com/v1"
    api_key_env: "OPENAI_API_KEY"
    models: ["gpt-4o", "gpt-4o-mini", "text-embedding-3-small"]
    rate_limit: { rpm: 3500, tpm: 60000 }
```

### Anthropic

```yaml
  anthropic:
    provider: "anthropic"
    base_url: "https://api.anthropic.com/v1"
    api_key_env: "ANTHROPIC_API_KEY"
    models: ["claude-sonnet-4-20250514", "claude-haiku-4-5-20251001"]
    rate_limit: { rpm: 1000 }
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

LLMProxy automatically translates between provider formats:

- **OpenAI** ↔ **Anthropic**: Messages format, system prompt handling, streaming events
- **OpenAI** ↔ **Google**: Content parts, role mapping, safety settings
- **OpenAI** ↔ **Azure**: Deployment URL construction, API version headers
- **OpenAI** ↔ **Ollama**: Direct pass-through (Ollama uses OpenAI format)

Multimodal content (images) is also translated:
- Anthropic: `base64` or `url` → `source` format
- Google: `inlineData` or `fileData` format
- MIME type auto-detection
