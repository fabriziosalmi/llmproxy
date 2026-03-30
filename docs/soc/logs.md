# Live Logs

The Live Logs view provides a real-time terminal with JSON-formatted log output.

![Live Logs](/screenshots/soc-logs.png)

## Terminal

Built with **xterm.js** and the WebGL renderer for high-performance log rendering:

- **Real-time streaming** via Server-Sent Events (SSE)
- **JSON syntax highlighting** — keys, values, and timestamps are color-coded
- **Font**: JetBrains Mono (primary), Fira Code (fallback)
- **Auto-scroll** — follows new log entries

## Log Content

The terminal shows structured JSON logs including:

- Request/response metadata
- Security events (injection, PII, firewall)
- Plugin execution traces (ring, latency, action)
- Circuit breaker state changes
- Budget consumption updates
- Error traces

## API

The SSE stream can be consumed directly:

```bash
curl -N http://localhost:8090/api/v1/logs \
  -H "Authorization: Bearer your-key"
```

Each event is a JSON object:

```json
{
  "timestamp": "2024-03-20T14:30:00Z",
  "level": "info",
  "module": "plugin_engine",
  "message": "PRE_FLIGHT ring completed",
  "data": {
    "plugins_executed": 4,
    "total_latency_ms": 12.5
  }
}
```
