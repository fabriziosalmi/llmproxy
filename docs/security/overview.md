# Security overview

What stands between a request and a provider, in the order it runs, and what each
part does and does not do. Defaults are the shipped configuration.

```
admission control        bounds concurrent requests
rate limiter             off by default
byte firewall            before authentication
control-plane auth       /api/v1/*, /admin/*, /metrics
data-plane auth          /v1/*, in the route
shield                   injection scoring, trajectory
plugin rings             PII masking, budget, routing
upstream
tool policy              off by default
post-flight ring         response sanitisation
```

## Authentication and access

Two tiers of bearer key. Inference keys (`LLM_PROXY_API_KEYS`) reach `/v1/*`. Admin
keys (`LLM_PROXY_ADMIN_KEYS`) reach `/api/v1/*`, `/admin/*` and `/metrics`. **If no
admin key is configured, inference keys are accepted on the control plane**; the
proxy warns at start and does not refuse to run.

Every path under `/api/v1/` and `/admin/` is denied unless it carries a valid
credential. The exceptions are `/health`, `/ready`, and three identity routes
(`/api/v1/identity/config`, `/exchange`, `/me`). The interactive API docs are
disabled while authentication is on.

Signed-in users (OIDC/JWT; off by default) are mapped to one of four roles: admin,
operator, user, viewer. See [Identity](/security/identity).

A Tailscale identity lookup, when available, adds the peer's user and node to the
log. It does not grant or deny access.

## Byte firewall

Matches signatures against the request body after decoding common encodings, and
answers `403` without invoking the application. It runs before authentication, so a
blocked request is not attributed to a caller and is not in the audit chain. See
[ASGI firewall](/security/firewall).

## Shield

Scores the prompt with regular expressions and a character-trigram comparison
against a list of known phrasings, and keeps a per-session trajectory. A request it
refuses is a row in the audit chain with the reason. See
[Injection scoring](/security/injection-scoring).

Both of these are lexical. How much they stop is in the
[benchmark](/security/benchmark): about one attack in five on data they were not
written against, and none of the instructions planted in a tool result.

## Tool policy

Off by default. Decides which tools a response may call, and which may be called in
a turn that follows a tool result. This is the control that addresses indirect
injection, because it does not depend on recognising the injected text. See
[Tool policy](/security/tool-policy).

## PII masking

On by default, in the pre-flight ring: regular expressions, or Presidio when
installed. Applied to chat messages, including text content parts. Not applied to
`/v1/embeddings` input. See [PII detection](/security/pii-detection).

## Response sanitisation

On by default for non-streaming responses: injection patterns, invisible
characters, and links to block-listed domains. **Streamed responses are not
sanitised.** A mid-stream guard scans the text as it arrives and can cut the stream.

## Audit chain

Every request that reaches the pipeline is recorded, served or not. What the chain
proves, with and without a key, and what stays outside it, is set out in the
[threat model](/threat_model#_3-3-repudiation).

## Plugins

Python plugins run inside the proxy's process. The loader rejects a plugin whose
source imports a short list of dangerous modules; that is a check against mistakes,
not a sandbox. Every plugin runs under a timeout and declares whether its failure
refuses the request (`fail_policy: closed`) or lets it through. WASM plugins require
the `extism` package, which the published image does not include.

## Outbound requests

Webhook deliveries resolve the destination and refuse private and reserved address
ranges at connect time; they are signed with HMAC-SHA-256 when a secret is
configured. Endpoints registered through the API are not restricted to public
addresses: registering an endpoint is an operator action.

## Not provided

- No rate limiting unless `rate_limiting.enabled` is set.
- No mutual TLS. Terminate TLS in front of the proxy.
- No protection for more than one replica: state is per process.
- No independent security review has been carried out.
