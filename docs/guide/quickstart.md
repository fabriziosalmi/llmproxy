# Quick Start

## One command

```bash
git clone https://github.com/fabriziosalmi/llmproxy && cd llmproxy
./install.sh
```

The installer detects the platform, checks prerequisites, creates `.env` from `.env.example` with a generated inference key (`LLM_PROXY_API_KEYS`), and starts the service with Docker Compose v2 or in a local Python 3.12+ virtualenv. An existing `.env` is left untouched. For CI or scripted use, `./install.sh --docker`, `./install.sh --local`, `./install.sh --yes` (Docker if available, otherwise the virtualenv) or `./install.sh --check` skip the interactive prompt.

The installer does not generate an admin key. While `LLM_PROXY_ADMIN_KEYS` is unset, the inference key is also accepted on the control plane (`/api/v1/*`, `/admin/*`, `/metrics`) and the proxy logs a warning at startup. Set `LLM_PROXY_ADMIN_KEYS` in `.env` to separate the two tiers.

### Prerequisites

| Install path | Requirements |
|---|---|
| **Docker** | Docker Engine + **Docker Compose v2 plugin** (`docker compose`). Legacy `docker-compose` v1 is unsupported — it breaks against modern urllib3. On Debian/Ubuntu: `sudo apt install docker-compose-plugin`. |
| **Local venv** | Python 3.12+ (Ubuntu 22.04 ships 3.10 — use [deadsnakes PPA](https://launchpad.net/~deadsnakes/+archive/ubuntu/ppa) or the Docker path). `npm` is optional: without it the installer skips the UI build and the admin UI is served unstyled. |

`./install.sh --check` reports what is missing and how to install it, and exits without installing anything.

## Onboarding mode

The proxy starts without any provider key. Only `LLM_PROXY_API_KEYS` is required: with authentication on (the shipped default) and that variable unset or left at the `.env.example` placeholder, the process exits at startup.

The shipped `config.yaml` declares endpoints for the cloud providers and one for Ollama at `http://localhost:11434`. An endpoint that names an `api_key_env` is registered for routing only when that variable is set. The `ollama` endpoint needs no key, so it is always registered, whether or not Ollama is running. A request for a model that no registered endpoint lists is answered with 503, unless a registered endpoint has an empty `models` list; such an endpoint takes any model.

The startup log and the banner say "onboarding mode" only when the configuration has no endpoint, or none with a usable credential. With the shipped `config.yaml` that does not occur, because of the `ollama` entry.

`/health`, the admin UI and `POST /api/v1/registry` answer as soon as the process is up. A provider can be added in four ways.

### 1. Auto-discovery (zero config)

At startup, and then every 300 seconds, the proxy probes `127.0.0.1` and `host.docker.internal` on the default ports of Ollama (11434), LM Studio (1234), vLLM (8000) and LiteLLM (4000). A service that answers with at least one model is registered. No YAML, no env var.

A local Ollama found this way is registered as `ollama-auto` when the id `ollama` is already taken, which is the case with the shipped `config.yaml`.

To extend discovery to remote hosts (Tailscale peers, LAN nodes), set:

```bash
# in .env
LLM_PROXY_DISCOVERY_PEERS=100.98.112.23,100.66.12.82,100.108.97.78:8000
```

Each entry can be a bare host (probes all four standard ports) or `host:port` (probes that port only, against every supported protocol signature). An endpoint discovered on a remote peer gets an id built from the service and the host, such as `lmstudio-100-98-112-23`.

Disable entirely with `LLM_PROXY_LOCAL_DISCOVERY=0`.

### 2. Env-declared endpoints (no YAML)

Declare any OpenAI-compatible endpoint directly in `.env`:

```bash
LLM_PROXY_ENDPOINT_LOCAL_URL=http://192.168.1.50:1234/v1
LLM_PROXY_ENDPOINT_LOCAL_MODELS=llama-3.3-70b,qwen-2.5-coder-32b
# LLM_PROXY_ENDPOINT_LOCAL_KEY=sk-…    # leave unset for no-auth servers
```

The env-declared endpoint becomes the `local` endpoint on the next start.

### 3. Cloud provider keys

Set any of the keys named in `config.yaml` (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, …) in `.env`. The matching endpoint is registered on the next start. No YAML edit is needed.

### 4. Admin UI

Open `http://localhost:8090/ui`, go to **Endpoints**, and fill the form: name, base URL, provider, priority, optional API key and model list. The admin UI calls `/api/v1/*`, so it needs an admin key (or the inference key, while `LLM_PROXY_ADMIN_KEYS` is unset). A new entry is routable without a restart.

An API key typed into this form is held in process memory only: it is used until the process restarts, and after a restart the endpoint is still listed but its requests go out without a key. For a provider you intend to keep, declare the endpoint in `.env` or `config.yaml` instead.

## Boot banner

On startup the proxy prints a summary to stdout — visible in `docker compose logs llmproxy` or the terminal. With the shipped `config.yaml`, one inference key and no provider key:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  LLMProxy is ready   http://localhost:8090/v1
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Active providers (1):
    [config]          ollama         (ollama)
      llama3.3, qwen3, phi-4, +3 more

  WAF:    ON   (byte-level ASGI injection firewall)
  Auth:   required   (1 Bearer key(s) configured in $LLM_PROXY_API_KEYS)

  Smoke test:
    curl http://localhost:8090/v1/chat/completions \
      -H 'Authorization: Bearer $(grep -oP '^LLM_PROXY_API_KEYS=\K[^,]+' .env | head -1)' -H 'Content-Type: application/json' \
      -d '{"model":"llama3.3","messages":[{"role":"user","content":"hi"}]}'

  Admin UI: http://localhost:8090/ui
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

"Active providers" lists the endpoints that need no key or whose key variable is set, each tagged with its source (`[config]`, `[env]`, `[auto-discovery]`). The smoke-test command uses the first model of the first listed provider. As printed, it nests single quotes, so the shell does not substitute the key; use the command under [First request](#first-request) instead.

## First request

```bash
# Your proxy key was generated by install.sh; read the first one from .env:
export LLMPROXY_KEY=$(grep '^LLM_PROXY_API_KEYS=' .env | cut -d= -f2 | cut -d, -f1)

curl http://localhost:8090/v1/chat/completions \
  -H "Authorization: Bearer $LLMPROXY_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

`gpt-4o` is listed under the `openai` endpoint of the shipped `config.yaml`, so this example needs `OPENAI_API_KEY` set. With a local provider, use one of the model names the banner shows.

### Model aliases

Shorthand names resolve to real model ids:

```bash
curl http://localhost:8090/v1/chat/completions \
  -H "Authorization: Bearer $LLMPROXY_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model": "fast", "messages": [{"role": "user", "content": "Hello!"}]}'
```

The shipped `config.yaml` defines, among others: `gpt4` → `gpt-4o`, `claude` → `claude-sonnet-4-6`, `fast` → `gpt-5.4-mini`, `cheap` → `gemini-2.5-flash-lite`. The full list is the `model_aliases` section.

### Cross-provider fallback

When the selected endpoint has an open circuit, cannot be reached, or answers 429, 500, 502, 503 or 504, the request is retried against the entries of `fallback_chains` for the requested model, in order. One of the chains in the shipped `config.yaml`:

```
gpt-5.4 → claude-opus-4-6 (anthropic) → gemini-2.5-pro (google)
```

A non-streaming request that times out while waiting for the response (`server.response_timeout`) is not retried on another provider; the caller gets 504.

A fallback attempt is sent with the fallback endpoint's own key, the one named by its `api_key_env`.

## Using with OpenAI SDK

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8090/v1",
    api_key="your-secret-key-1",
)

response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Hello!"}],
)
print(response.choices[0].message.content)
```

## Using with Cursor / Continue / OpenWebUI

Point any OpenAI-compatible client to:

```
Base URL: http://localhost:8090/v1
API Key:  (one of the keys in LLM_PROXY_API_KEYS)
```

`GET /v1/models` returns the models of every endpoint in the configuration, including endpoints whose key is not set.

## Disabling the WAF

The byte-level ASGI firewall is enabled by default. Disable it via env or config when fronting the proxy with another WAF or debugging a false positive:

```bash
LLM_PROXY_FIREWALL_ENABLED=0        # in .env — restart required
```

Or in `config.yaml`:

```yaml
security:
  firewall:
    enabled: false
```

Both are read when the process starts; a change takes effect after a restart. The environment variable wins over the file. The admin UI shows the current state and the reason it is off (`env:…` or `config:…`). There is no runtime switch for the firewall in the admin UI.

The body-size limit, the body deadline and the nesting-depth limit stay active when the firewall is disabled.

## Next steps

- [Configuration](/guide/configuration) — Guide to `config.yaml`
- [Endpoints](/reference/endpoints) — Provider matrix and env-based endpoint syntax
- [Security](/security/overview) — The security pipeline
- [Plugins](/plugins/overview) — Enable marketplace plugins
- [Admin UI](/soc/overview) — Monitoring views
