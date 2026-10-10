# Live Logs

The Live Logs screen is a terminal that prints the proxy's event log as it is produced. The event log is what the proxy records through its own event logger (for example `SYSTEM: Proxy service ACTIVE`, `TOOL POLICY: call to '...' refused`, `BUDGET SATURATED`); it is not the Python process log.

## Terminal

- Built with xterm.js. The WebGL renderer is used when the browser supports it, otherwise the default renderer
- Font: JetBrains Mono, then Fira Code, then the system monospace font
- 10 000 lines of scrollback
- Each line shows the time, the level in a colour, and the message. JSON found inside a message is printed indented and coloured
- A status label shows `connecting…`, `live` or `reconnecting in 5s…`. After a stream error the page reconnects after 5 seconds
- **Clear** empties the terminal. It does not affect the server

## Filters

- **Level buttons** `ERROR` and `SECURITY` show only entries of that level. Click again to remove the filter
- **`blocked` button** adds the word `blocked` to the text filter
- **Text filter**: an entry is shown when its level, message and JSON contain the text, ignoring case. Terms separated by `|` must all match
- The filters are stored in the URL fragment, for example `#/logs?log_level=ERROR&log_q=timeout`, so a filtered view can be linked

Filters are applied to entries as they arrive. An entry that was filtered out is not kept, so changing the filter does not bring it back.

## How the stream is opened

The browser cannot send an `Authorization` header on an event stream. It first calls `POST /api/v1/logs/token` with the admin key and receives a short-lived token (120 seconds unless `security.sse.token_ttl_seconds` says otherwise), then opens `/api/v1/logs?sse_token=...`.

On connection the server sends the most recent entries it holds (up to 200), then new entries as they are logged. The server accepts 20 concurrent log streams and answers 503 beyond that.

## API

The stream can be read directly with an admin key:

```bash
curl -N http://localhost:8090/api/v1/logs \
  -H "Authorization: Bearer your-key"
```

Each event is a JSON object:

```json
{
  "timestamp": "14:30:00",
  "level": "INFO",
  "message": "SYSTEM: Proxy service ACTIVE",
  "metadata": {}
}
```

`timestamp` is the server's local time as `HH:MM:SS`, without a date. An entry may also carry a `trace_id`.
