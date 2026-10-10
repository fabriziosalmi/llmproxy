# Models

The Models screen lists what `GET /v1/models` returns: the models declared under `endpoints.<name>.models` in `config.yaml`. A model id declared by more than one endpoint appears once. The list does not depend on whether an endpoint is reachable. It refreshes every 30 seconds.

## Counters

| Tile | What it shows |
|------|---------------|
| **Active Models** | Number of models in the list |
| **Providers** | Number of distinct `owned_by` values |
| **Embedding Models** | Number of models whose id matches one of the embedding name patterns in `ui/src/views/models/types.ts` (for example `text-embedding`) |

## Model tables

Chat models are listed first and embedding models in a second table. A search box filters both by model id or provider.

| Column | Content |
|--------|---------|
| **Model ID** | The model id, with an `EMB` badge on embedding models |
| **Provider** | `owned_by`: the endpoint's `provider`, or the endpoint name when no provider is set |
| **Actions** | Copy ID, and Inspect, which opens the model's detail panel |

The tables can be sorted by model id or provider.

## API

```bash
# OpenAI-compatible model list
curl http://localhost:8090/v1/models \
  -H "Authorization: Bearer your-key"

# One model
curl http://localhost:8090/v1/models/gpt-4o \
  -H "Authorization: Bearer your-key"
```
