# ASGI Firewall

The ASGI firewall (`core/firewall_asgi.py`) is the first line of defense — a byte-level L7 request filter running as ASGI middleware.

## How It Works

The firewall scans raw request body bytes for injection signatures **before** any JSON parsing or routing occurs. Malicious requests are terminated with an instant 403 response, preventing any LLM cost.

## Patterns

The firewall matches against 11 known injection patterns:

- `ignore previous instructions`
- `bypass guardrails`
- `reveal your system prompt`
- `disregard all prior`
- `override safety`
- And 6 more...

## Response

When a pattern is matched:

```json
{
  "error": "Request blocked by firewall",
  "type": "firewall_block"
}
```

HTTP status: **403 Forbidden**

## Limitations

::: warning
The ASGI firewall uses **static pattern matching only**. It is not a substitute for ML-based injection detection. Sophisticated prompt injection can bypass static patterns. Use it as the first layer in a defense-in-depth strategy alongside SecurityShield's injection scoring.
:::

## Configuration

The firewall is always enabled when the security module is active. No additional configuration is needed.

```yaml
security:
  enabled: true
```
