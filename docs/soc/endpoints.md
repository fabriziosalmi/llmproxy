# Endpoints View

The Endpoints view provides a management interface for the LLM endpoint registry.

![Endpoints View](/screenshots/soc-endpoints.png)

## Endpoint Table

Each configured endpoint is displayed with:

- **Name** -- Endpoint identifier from `config.yaml`
- **Provider** -- Adapter type (openai, anthropic, google, azure, ollama, etc.)
- **Status** -- Live (healthy), offline (failed health check), or discovered (auto-detected)
- **Models** -- Number of models served by this endpoint
- **Latency** -- Current EMA-weighted response latency
- **Circuit** -- Circuit breaker state (closed = healthy, open = failing)

## Actions

| Action | Description |
|--------|-------------|
| **Toggle** | Enable or disable an endpoint without removing it |
| **Delete** | Remove an endpoint from the registry |
| **Priority** | Set routing priority for endpoint selection |

## Circuit Breaker

Endpoints that fail repeatedly are automatically circuit-broken:

- **Closed** -- Endpoint is healthy, accepting traffic
- **Open** -- Endpoint has failed, traffic is routed to fallback chain
- **Half-Open** -- Testing recovery with a single probe request

Circuit state is visible per-endpoint and triggers `circuit_open` / `endpoint_recovered` webhook events.

## API

```bash
# Full registry state
curl http://localhost:8090/api/v1/registry \
  -H "Authorization: Bearer your-key"

# Toggle endpoint
curl -X POST http://localhost:8090/api/v1/registry/openai/toggle \
  -H "Authorization: Bearer your-key"

# Set priority
curl -X POST http://localhost:8090/api/v1/registry/openai/priority \
  -H "Authorization: Bearer your-key" \
  -d '{"priority": 1}'
```
