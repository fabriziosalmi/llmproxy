# Guards Panel

The Guards view provides control over the proxy and individual security features.

![Guards Panel](/screenshots/soc-guards.png)

## Master Proxy Toggle

A prominent switch to enable or disable the entire proxy. When disabled, all requests are rejected.

## Security Guards

Individual toggles for each security feature:

| Guard | Description |
|-------|-------------|
| **Injection Guard** | SecurityShield injection scoring |
| **Language Guard** | Language detection and filtering |
| **Link Sanitizer** | URL/link sanitization and blocked domain checking |

Each guard shows:
- Current state (enabled/disabled)
- Description of what it does
- Toggle control

## API

Guards are controlled via the features API:

```bash
# List current state
curl http://localhost:8090/api/v1/features \
  -H "Authorization: Bearer your-key"

# Toggle a guard
curl -X POST http://localhost:8090/api/v1/features/toggle \
  -H "Authorization: Bearer your-key" \
  -d '{"feature": "injection_guard", "enabled": true}'
```
