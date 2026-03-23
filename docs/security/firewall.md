# ASGI Firewall

The ASGI firewall (`core/firewall_asgi.py`) is the first line of defense -- a byte-level L7 request filter running as ASGI middleware.

## How It Works

The firewall scans raw request body bytes for injection signatures **before** any JSON parsing or routing occurs. Malicious requests are terminated with an instant 403 response, preventing any LLM cost.

## Patterns

The firewall matches against 11 known injection patterns:

1. `ignore previous instructions`
2. `ignore all previous`
3. `disregard previous instructions`
4. `bypass guardrails`
5. `bypass safety`
6. `you are a developer mode`
7. `you are now in developer mode`
8. `ignore your instructions`
9. `override your system prompt`
10. `reveal your system prompt`
11. `print your system prompt`

Matching is case-insensitive and operates on raw bytes.

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
The ASGI firewall uses **static pattern matching only**. It is not a substitute for ML-based injection detection. Sophisticated prompt injection can bypass static patterns. Use it as the first layer in a defense-in-depth strategy alongside SecurityShield's injection scoring and the Topic Blocklist plugin.
:::

## Configuration

The firewall is always enabled when the security module is active. No additional configuration is needed.

```yaml
security:
  enabled: true
```
