# Guards

The Guards screen has two switches for the proxy as a whole, a grid of eight guard cards, cache statistics and three reset actions. It refreshes from `GET /api/v1/guards/status` every 10 seconds.

## Gateway Status

A switch that enables or disables the proxy (`POST /api/v1/proxy/toggle`). While the proxy is disabled, inference requests are answered with HTTP 503 (`Proxy service is currently STOPPED.`). The state is saved in the store and survives a restart.

## Priority Steering

A switch that makes the router send every request to the highest-priority endpoint (`POST /api/v1/proxy/priority/toggle`).

## Guard cards

Three cards have a switch. The switch calls `POST /api/v1/features/toggle`, and the state is saved in the store.

| Card | What the switch changes |
|------|-------------------------|
| **Injection Guard** | The injection-pattern check on **responses** (`SecurityShield.sanitize_response`). It does not turn off the scoring of requests |
| **Language Guard** | The charset check on responses |
| **Link Sanitizer** | Nothing. The switch sets the `link_sanitizer` flag, and the link check reads `security.link_sanitization.enabled` from `config.yaml` |

Five cards are read-only: PII Masker, ASGI Firewall, Rate Limiter, Zero Trust and Circuit Breaker. The ASGI Firewall card shows whether the firewall is running and, when it is not, the reason reported by the backend. The other four show a fixed label.

The descriptions on the cards are fixed text in the UI source (`ui/src/views/guards/catalog.ts`). They are not read from the running configuration. For example, the Rate Limiter card is labelled as active middleware while rate limiting is off by default.

## Cache Performance

Counters of the negative cache (requests dropped, entries, TTL) and of the response cache (hit rate, entries, hits and misses), from `GET /api/v1/cache/stats`.

## Operations

| Button | Call | Effect |
|--------|------|--------|
| **Reset WAF Counters** | `POST /api/v1/firewall/reset` | Sets the firewall's scanned, blocked and per-signature counters to zero |
| **Clear Caches** | `POST /api/v1/cache/clear` | Empties the negative cache. In the response cache it removes expired entries only |
| **Reset Sessions & Ledger** | `POST /api/v1/security/reset` | Clears the shield's session memory and threat ledger |

## API

```bash
# Current state of the three switchable guards
curl http://localhost:8090/api/v1/features \
  -H "Authorization: Bearer your-key"

# Set one of them
curl -X POST http://localhost:8090/api/v1/features/toggle \
  -H "Authorization: Bearer your-key" \
  -H "Content-Type: application/json" \
  -d '{"name": "language_guard", "enabled": true}'
```

The feature names are `injection_guard`, `language_guard` and `link_sanitizer`. An unknown name is answered with HTTP 400.
