# Models View

The Models view provides an aggregated registry of all LLM models available across configured providers.

![Models View](/screenshots/soc-models.png)

## KPI Cards

| Card | Description |
|------|-------------|
| **Active Models** | Total models available across all live providers |
| **Providers** | Number of configured LLM providers |
| **Embedding Models** | Models supporting the embeddings endpoint |

## Model Registry

A searchable table listing every model from every configured provider:

- **Model ID** -- Full model identifier (e.g. `gpt-4o`, `claude-sonnet-4-20250514`)
- **Provider** -- Which endpoint serves this model
- **Type** -- Chat, completion, or embedding
- **Status** -- Live, discovered, or offline

Models are aggregated automatically from all configured endpoints in `config.yaml`. The `/v1/models` API endpoint serves the same data in OpenAI-compatible format for clients like Cursor, Continue, and OpenWebUI.

## API

```bash
# OpenAI-compatible model discovery
curl http://localhost:8090/v1/models \
  -H "Authorization: Bearer your-key"

# Single model info
curl http://localhost:8090/v1/models/gpt-4o \
  -H "Authorization: Bearer your-key"
```
