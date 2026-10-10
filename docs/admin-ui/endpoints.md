# Endpoints

The Endpoints screen shows the endpoint registry (`GET /api/v1/registry`) and lets an operator add, test, toggle and delete endpoints. The registry is fetched every 30 seconds.

## Endpoint table

| Column | Content |
|--------|---------|
| **Endpoint** | Name (the host of the endpoint's URL) and the URL |
| **Status** | `Live` for a verified endpoint; otherwise the registry status (`FOUND`, `IGNORED`, `DISCOVERED`) |
| **Circuit** | Circuit breaker state: `CLOSED`, `OPEN` or `HALF` |
| **Latency** | The latency stored for the endpoint, or `--` |
| **Priority** | The endpoint's priority, with buttons to lower and raise it |
| **Actions** | See below |

## Actions

| Action | Call | Effect |
|--------|------|--------|
| **Copy cURL** | none | Copies a curl command for the endpoint |
| **Test** | `POST /api/v1/registry/{id}/probe` | Sends a model-listing request to the endpoint (no inference) and stores the measured latency |
| **Inspect** | none | Opens the endpoint's detail panel |
| **Reset CB** | `POST /api/v1/circuit-breaker/{id}/reset` | Resets the endpoint's circuit breaker |
| **Toggle** | `POST /api/v1/registry/{id}/toggle` | Sets a verified endpoint to `IGNORED`, and any other endpoint to verified |
| **Delete** | `DELETE /api/v1/registry/{id}` | Removes the endpoint from the registry, after a confirmation |
| **Priority − / +** | `POST /api/v1/registry/{id}/priority` | Sets the endpoint's priority |

## Adding an endpoint

"+ Add Endpoint" opens a form with name, base URL, provider, priority, API key (optional) and a comma-separated list of models. It posts to `POST /api/v1/registry`. "Scan local" probes well-known local ports (`POST /api/v1/registry/scan`) and fills in the form.

## Circuit breaker

Each endpoint has a circuit breaker (`core/circuit_breaker.py`):

- **Closed**: the endpoint takes traffic
- **Open**: the endpoint has reached the failure threshold; the router leaves it out
- **Half-open**: after the recovery timeout, one probe request is let through

The defaults in the code are 5 failures and 60 seconds. The proxy dispatches the webhook events `circuit_open` and `endpoint_recovered`.

## API

```bash
# The registry
curl http://localhost:8090/api/v1/registry \
  -H "Authorization: Bearer your-key"

# Toggle an endpoint
curl -X POST http://localhost:8090/api/v1/registry/openai/toggle \
  -H "Authorization: Bearer your-key"

# Test an endpoint without inference
curl -X POST http://localhost:8090/api/v1/registry/openai/probe \
  -H "Authorization: Bearer your-key"

# Set the priority
curl -X POST http://localhost:8090/api/v1/registry/openai/priority \
  -H "Authorization: Bearer your-key" \
  -H "Content-Type: application/json" \
  -d '{"priority": 1}'
```
