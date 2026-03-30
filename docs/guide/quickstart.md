# Quick Start

Get LLMProxy running in under 5 minutes.

## Prerequisites

- Python 3.12+
- At least one LLM provider API key (OpenAI, Anthropic, etc.)

## Installation

```bash
# Clone the repository
git clone https://github.com/fabriziosalmi/llmproxy
cd llmproxy

# Install dependencies
pip install -r requirements.txt

# Copy environment template
cp .env.example .env
```

## Configuration

Edit `.env` with your API keys:

```bash
# Required: at least one provider
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=AIza...

# Required: proxy authentication
LLM_PROXY_API_KEYS=your-secret-key-1,your-secret-key-2
```

## Start the Proxy

```bash
python main.py
```

The proxy starts on port **8090** by default:

- **API**: `http://localhost:8090/v1/chat/completions`
- **SOC Dashboard**: `http://localhost:8090/ui`
- **Health**: `http://localhost:8090/health`
- **Metrics**: `http://localhost:8090/metrics`

## First Request

Send a request using the OpenAI-compatible API:

```bash
curl http://localhost:8090/v1/chat/completions \
  -H "Authorization: Bearer your-secret-key-1" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

### Using Model Aliases

LLMProxy supports shorthand aliases:

```bash
# "fast" resolves to gpt-4o-mini
curl http://localhost:8090/v1/chat/completions \
  -H "Authorization: Bearer your-secret-key-1" \
  -H "Content-Type: application/json" \
  -d '{"model": "fast", "messages": [{"role": "user", "content": "Hello!"}]}'
```

Default aliases: `gpt4` → gpt-4o, `claude` → claude-sonnet, `fast` → gpt-4o-mini, `cheap` → gemini-2.0-flash

### Cross-Provider Fallback

If OpenAI is down, LLMProxy automatically falls back to the next provider in the chain:

```
gpt-4o → claude-sonnet → gemini-2.5-pro
```

No client-side changes needed.

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
API Key: your-secret-key-1
```

LLMProxy exposes `/v1/models` for automatic model discovery.

## Next Steps

- [Configuration](/guide/configuration) — Full config.yaml reference
- [Security](/security/overview) — Understanding the security pipeline
- [Plugins](/plugins/overview) — Enable marketplace plugins
- [SOC Dashboard](/soc/overview) — Real-time monitoring
