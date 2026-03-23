# Endpoints Reference

LLMProxy supports 15 LLM providers with automatic request/response translation.

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
