# ASGI Firewall

`core/firewall_asgi.py`. A filter on the raw bytes of the request body, run as ASGI
middleware before authentication and before the body is parsed.

## What it does

- Reads the whole body, within a deadline (30 s by default) and a size limit
  (512 KiB by default). A body that is too slow gets `408`, one that is too large
  `413`, one nested deeper than the permitted depth `400`.
- Matches the body against the signatures in `data/signatures.yaml`: 162 phrases
  and 16 ROT13 forms. Before matching it decodes URL encoding, Unicode escapes,
  Base64, hex and ROT13, repeatedly, so a phrase wrapped in several encodings is
  still found.
- On a match, answers without invoking the application:

```json
{"error": "request_blocked", "message": "Blocked by injection guard"}
```

HTTP status `403`, and the connection is closed.

## What it does not do

- **It is a list of known phrases.** A reworded attack does not match. On the
  [benchmark](/security/benchmark) the firewall and the shield together stop about
  one attack in five.
- **It matches bytes, not parsed text.** A phrase split across message parts, or
  written with JSON escapes it does not decode, is not seen here. The shield, which
  works on the parsed request, is the second chance.
- **A blocked request is not in the audit chain.** The firewall runs before
  authentication, so there is no caller to attribute the request to. Blocks are
  counted in the metrics and written to the process log.
- **It can refuse legitimate text** that quotes one of the phrases, such as a
  question about prompt injection.

## Configuration

On by default. To turn it off (for example behind another WAF):

```bash
LLM_PROXY_FIREWALL_ENABLED=0
```

or

```yaml
security:
  firewall:
    enabled: false
```

The admin UI shows whether it is on. It cannot be switched off from the UI.

The signatures are read from `data/signatures.yaml`. That directory is also where the
data volume is mounted, and a bind mount or an empty volume hides the file shipped in
the image; the image therefore keeps a second copy in `/app/defaults`, which is used
when the one in `data/` is absent (the proxy says so at start). A file you place in
`data/` takes precedence and is reloaded when it changes. Note that a Docker *named*
volume is filled from the image when it is first created and never again: after an
upgrade it still holds the previous release's file, so delete `data/signatures.yaml`
from the volume to return to the shipped one.
