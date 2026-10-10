# LLMProxy — Threat Model

A threat model for LLMProxy. Each control names the module that implements it,
and defaults are those of the shipped configuration. Where a control is partial,
off by default or absent, this page says so.

- Method: STRIDE, one section per category, and a mapping to the OWASP LLM Top 10
  (2025).
- How much the detection stops is measured in the
  [detection benchmark](/security/benchmark). This page does not give coverage
  figures of its own.
- Regression corpus: [OWASP_LLM_COVERAGE.md](OWASP_LLM_COVERAGE.md), written by
  `pytest tests/test_owasp_corpus.py`. See §5 for what it is and is not.

---

## 1. Scope & trust boundaries

LLMProxy is an OpenAI-compatible gateway that sits between untrusted callers and
one or more upstream LLM providers. It is itself a security control plane, so its
own attack surface is in scope.

```
        (untrusted)                    ┌─────────────── LLMProxy trust boundary ───────────────┐
 client ─── HTTP ──▶ ASGI byte firewall ─▶ auth/RBAC ─▶ SecurityShield ─▶ plugin rings ─▶ forwarder ─── HTTPS ──▶ upstream LLM
                     (firewall_asgi.py)   (rbac.py,     (security.py)      (plugin_engine)  (forwarder.py)          (provider)
                                           identity.py)
                                                          │                                        │
                                                   threat ledger,                            response signer,
                                                   session memory                            audit hash-chain
                                                   (threat_ledger.py)                         (response_signer.py)
```

**Trust boundaries crossed:** (a) network → firewall; (b) firewall → authenticated
identity; (c) authenticated request → security inspection; (d) inspected request →
upstream provider; (e) response → recorded in the audit log, and signed when a
signing key is configured, before return. State stores are inside the boundary:
the SQLite or PostgreSQL database that holds the audit log, and Redis, when one
is configured, for rate-limit and circuit-breaker state. In scope: `/v1/*`
inference, `/api/v1/*` admin, plugin loader, state stores. Out of scope: the
upstream model's own behavior, the caller's host, and the build pipeline (the
image build attaches an SBOM and a provenance attestation,
`.github/workflows/docker.yml`).

---

## 2. The request pipeline

A request passes through several checks, and any one of them can refuse it. The
firewall and the shield are both lexical: an attack worded in a way neither
recognises passes both. The [benchmark](/security/benchmark) measures how often
that happens.

| # | Control | Module | What it does | Default |
|---|---------|--------|--------------|---------|
| L0 | **ASGI byte firewall** | `core/firewall_asgi.py` | Matches the raw body against 162 phrases and 16 ROT13 forms from `data/signatures.yaml`, after decoding URL encoding, Unicode escapes, Base64, hex and ROT13, repeatedly. Runs before authentication and before the body is parsed. | on |
| L1 | **Authentication and roles** | `proxy/app_factory.py`, `proxy/auth_helpers.py`, `core/rbac.py`, `core/identity.py` | API keys in two tiers (inference, admin). Control-plane paths are refused without a credential. OIDC/JWT sign-in (RS256 or ES256) with four roles is optional. | keys on; OIDC off |
| L2 | **Tailscale lookup** | `core/zero_trust.py` | Asks the local Tailscale daemon who the peer is and writes the user and node to the log. It does not grant or deny access. Mutual TLS is not implemented. | runs when Tailscale is present |
| L3 | **Injection scoring** | `core/security.py`, `core/semantic_analyzer.py`, `core/confidence.py` | 30 regular expressions (seven of them instruction-override phrases in other languages) over the raw and the normalised text. A pattern score of 0.85 or more refuses the request. Below that, a composite of pattern score, character-trigram similarity to 157 known phrasings, and session trajectory decides. See [Injection scoring](/security/injection-scoring). | on |
| L4 | **PII masking** | `plugins/default/pii_masker.py`, `core/security.py` (`mask_pii`) | Replaces emails, US SSNs, card numbers, IBANs, phone numbers, IPv4 addresses and API-key-shaped strings in chat messages with placeholders, restored in non-streaming responses. Regular expressions, or Presidio when installed. Not applied to `/v1/embeddings`. | on |
| L5 | **Plugin rings** | `core/plugin_engine.py` | Ingress, pre-flight, routing, post-flight and background hooks. Python plugins run inside the proxy's process and are **not sandboxed**. The loader checks imports against an allow-list (a lint), verifies a SHA-256 pin when the manifest records one, confines entrypoints to `plugins/`, and refuses in-process Python plugins from the installed manifest unless they opt in. | on |
| L6 | **Trajectory and ledger** | `core/security.py`, `core/threat_ledger.py` | Refuses a request when the session's last three pattern scores (within 5 minutes) add up to more than 1.5, or when one address or credential accumulates a score of 3.0 over at least 3 requests in 600 seconds. In memory, per process. | on |
| L7 | **Tool policy** | `core/tool_policy.py` | Decides which tools a response may call, and which may be called in a turn that follows a tool result. See [Tool policy](/security/tool-policy). | off |
| L8 | **Response signing and audit** | `core/response_signer.py`, `store/audit_chain.py` | HMAC-SHA-256 signature headers on non-streaming responses when a signing key is configured. A hash-chained audit log, checked by `/api/v1/audit/verify`. Rows are removed by retention and erasure, and the chain records each removal; what the chain does and does not show is in §3.3. | signing off; audit on |

Rate limiting (`core/rate_limiter.py`) is off by default. Admission control
(`core/admission.py`), which bounds concurrent `/v1/` requests, is on.

---

## 3. STRIDE analysis

### 3.1 Spoofing (identity)
**Threats:** posing as a legitimate client or admin; forging upstream identity.
- **Controls:** API keys compared in constant time, in two tiers
  (`proxy/auth_helpers.py`): `LLM_PROXY_API_KEYS` for `/v1/*` and
  `LLM_PROXY_ADMIN_KEYS` for the control plane. A middleware
  (`proxy/app_factory.py`) refuses `/api/v1/*`, `/admin/*` and `/metrics` without
  a credential while authentication is on, which is the default, and then checks
  the caller's role against the permission the route needs
  (`core/control_plane_policy.py`). Rejected credentials are counted in the
  metrics (`llm_proxy_auth_failures_total`) and logged. Optionally, OIDC/JWT
  sign-in (`core/identity.py`: RS256 or ES256, verified against the provider's
  JWKS).
- **Residual:**
  - API keys are the default and the only credential unless `identity.enabled` is
    set. Whoever holds a key is the caller; rotation and distribution are the
    operator's.
  - If `LLM_PROXY_ADMIN_KEYS` is not set, every inference key is accepted on the
    control plane. The proxy warns at start and runs.
  - With OIDC on, a role named in a token's `roles` claim is honoured, so the
    identity provider decides who is an administrator unless `role_mappings`
    lists the user.
  - The Tailscale lookup (`core/zero_trust.py`) only adds the peer's user and
    node to the log. It authenticates nobody. Mutual TLS is not implemented.

### 3.2 Tampering (data / instructions)
**Threats:** prompt injection (OWASP LLM01); corrupting ledger/config state.
- **Controls:** L0 and L3 above. The shield applies its patterns to the raw text
  and to a normalised form (Unicode NFKC, zero-width characters removed,
  look-alike Cyrillic and Greek letters and leetspeak digits mapped to Latin), and
  has patterns for instruction-override phrases in seven other languages. The
  tool policy (off by default) limits which tools a response may call, without
  depending on recognising the injected text. Configuration edits through the API
  (`proxy/routes/config.py`) need the `proxy:config` permission, are validated
  before an atomic write, and are backed up; a change that lowers the security
  posture needs a confirm token. The audit log is hash-chained (§3.3).
- **Residual:**
  - Detection is lexical. On the [benchmark](/security/benchmark) the firewall and
    the shield stop 21% of 3,035 attack prompts and none of 1,054 instructions
    planted in a tool result. An attack that is reworded, or that arrives as
    ordinary text in a tool result, is not recognised.
  - The code can pass a borderline request to a language model for a verdict. No
    model is wired to the shield in the shipped proxy; a fixed threshold is used
    instead.
  - Configuration applies, feature toggles and plugin installs are written to the
    operator log, which is held in memory. They are not rows in the audit chain.
  - Rate-limit and circuit-breaker state is in the process's memory unless Redis
    is configured (`rate_limiting.redis_url`; `caching.redis_url` or `REDIS_URL`).
    The shield's session memory and the threat ledger are always per process, so
    with more than one replica each keeps its own.

### 3.3 Repudiation
**Threats:** a malicious action (e.g. budget drain) with no provable trail.
- **Controls:** `EventLogger` records SECURITY/SYSTEM events; the audit ledger is
  a **hash chain** verifiable via `/api/v1/audit/verify` (what it does and does
  not prove is spelled out below). `ResponseSigner` can add an HMAC to
  non-streaming responses when a signing key is configured (off by default); it is
  a shared-key signature, so it shows the holder of the key that a response was
  not altered and proves nothing to a third party.
- **Sessions can be revoked.** A proxy-issued session JWT carries its roles until
  it expires, so an administrator can end one early: `POST /api/v1/identity/revoke`
  by `jti` (one token) or by subject (every session issued so far). The list is
  persisted and reloaded at startup, and checked inside `verify_proxy_jwt`, which
  both the data plane and the control plane use. It does not reach the identity
  provider: the person can obtain a new session by signing in again unless their
  access is also removed there.
- **What the audit log holds.** One row per request that reached the pipeline,
  served or not: a request the shield or a plugin refused is a row with
  `blocked = 1`, its status and the reason the caller was given; a request that
  failed upstream is a row with its 5xx. `/v1/embeddings` is recorded like chat.
  Rows carry metadata (who, model, provider, status, tokens, cost, latency), not
  prompts or responses. **Not in the chain:** requests rejected before the
  pipeline (a missing or wrong key, the rate limiter, the byte-level firewall,
  which runs before authentication) and control-plane changes (a configuration
  apply, a feature toggle, a plugin install). Those show up in the metrics, the
  process log and the in-app security feed (a ring buffer in memory); none of
  that is tamper-evident.
- **Who a row is attributed to.** `key_prefix` is the first eight characters of
  the API key, or the signed-in user's email (or subject) for an identity token;
  `session_id` is an HMAC of the credential. Two keys that share their first
  eight characters share a `key_prefix`, and `session_id` is stable across
  restarts only when `LLM_PROXY_IDENTITY_SECRET` is set.
- **Legitimate deletions are recorded in the chain.** The retention purge and
  GDPR erasure remove audit rows; each appends a row (`audit.rows_removed`) with
  the hash before the removed run and the hash of its last row, no row content.
  `/api/v1/audit/verify` bridges a break only when such a row, itself verified,
  accounts for it, and lists every removal with its reason, time and row count
  (`removals`, `rows_removed`). The record used to be a value in `app_state`,
  beside the table and covered by no hash: whoever could delete a row could
  write the record that excused it. It is no longer read from there.
- **Without a key the chain detects accidents, not a database writer.** Rows
  are sealed with SHA-256 over a canonical encoding (format 2). Someone who can
  write the database can recompute it, and can append a well-formed removal
  record for rows they deleted: the verifier then answers `valid`, with the
  removal listed. That is evidence for whoever reads the list, not a refusal.
- **With `LLM_PROXY_AUDIT_KEY` the chain is keyed** (HMAC-SHA-256, format 3).
  Someone who can write the database and does not hold the key cannot alter a
  row, remove one, or append one (a removal record included) without
  `/api/v1/audit/verify` failing. The key must live where the database's
  writers cannot read it; kept in the same place, it adds nothing. A keyed
  chain cannot be continued unkeyed: a row in an older format after a newer one
  is a break, so removing the key is itself detected. Retired keys go in
  `LLM_PROXY_AUDIT_KEY_PREVIOUS` for as long as rows sealed with them are
  retained.
- **The head can be kept outside the database.** `GET /api/v1/audit/head` and an
  hourly `AUDIT HEAD` line in the process log (standard error, so the container log)
  give the chain's newest row. It is out of the database writers' reach only
  where you put it: ship the log off the host, or record the head yourself;
  `/api/v1/audit/verify?anchor_id=&anchor_hash=` checks the chain against one. That
  catches a consistent rewrite of rows before the anchor and a truncation of the
  tail, keyed or not, and reports how many rows have been removed since
  (`anchor.rows_removed_since`). It does not catch rows forged *after* the
  newest anchor on an unkeyed chain, nor a rollback of the whole database to an
  earlier state that matches an older anchor.
- **The process itself is trusted.** Whoever can run code as the proxy, or read
  its environment, holds the key and can write any row. The chain is evidence
  against the database and its backups being edited, not against the host.

### 3.4 Information disclosure
**Threats:** PII regurgitation; secret/stack-trace leakage.
- **Controls:** PII masking of chat messages (L4). API keys appear in audit and
  spend rows only as their first eight characters. The runtime configuration view
  (`/api/v1/config/yaml`) and the GDPR export pass through a scrubber that redacts
  fields named like keys, tokens, secrets and passwords (`core/export.py`). An unhandled exception returns a
  generic `500` body with no traceback (`proxy/error_envelope.py`). The `Server`
  header is `llmproxy`, not the web server's default.
- **Residual:**
  - Masking recognises a fixed set of patterns, in chat messages only. It is not
    applied to `/v1/embeddings`, to tool definitions or to tool-call arguments.
    See [PII detection](/security/pii-detection).
  - Streamed responses are not sanitised and their placeholders are not restored.
  - `/api/v1/config/raw` returns `config.yaml` as it is on disk. A secret written
    into that file, instead of referenced through an environment variable, is
    returned to whoever holds `proxy:config`.
  - Two keys that share their first eight characters are indistinguishable in the
    audit log.
- **Data subject requests:** export and erasure by subject (`/api/v1/gdpr/*`,
  see the [Admin API](/api/admin#data-protection-gdpr)). These are tools for
  answering a request; the page makes no statement about legal compliance.

### 3.5 Denial of service
**Threats:** payload flooding; wallet-exhaustion.
- **Controls:**
  - Body size limit (`security.max_payload_size_kb`, 512), nesting-depth limit
    and a deadline for receiving the body, all in the firewall, before
    authentication.
  - Admission control (`core/admission.py`): a bound on concurrent `/v1/`
    requests and on the queue behind them, then `503` with `Retry-After`.
  - Budget: a request whose estimated cost would take the day's spend over
    `budget.daily_limit`, or whose key has exhausted its quota, is refused with
    `402` before the provider is called.
  - The shield's patterns are compiled once, and their wildcard spans are
    bounded, for example `.{0,40}` (`_THREAT_PATTERNS` in `core/security.py`).
- **Residual:**
  - **Rate limiting is off by default** (`rate_limiting.enabled`). When on, it is
    a token bucket per credential, or per client address when no bearer token is
    sent, held in memory or in Redis when `rate_limiting.redis_url` is set.
  - There is no cap on `max_tokens` unless the `Max Tokens Enforcer` plugin is
    enabled; it is off by default.
  - The budget is enforced per process. Several replicas each enforce the full
    limit.

### 3.6 Elevation of privilege
**Threats:** RCE via the plugin loader; path traversal on admin endpoints.
- **Controls:** the plugin loader (`core/plugin_engine.py`) checks a plugin's
  imports against an allow-list (`ALLOWED_MODULES`), verifies a SHA-256 pin when
  the manifest records one, confines every entrypoint to `plugins_dir` after
  resolving symlinks, and refuses an in-process Python plugin from the installed
  manifest unless its entry sets `allow_inprocess: true`. Plugin management needs
  the `plugins:manage` permission. The export download route confines the path to
  the export directory. Roles (`core/rbac.py`) limit what a signed-in user may do
  on the control plane.
- **Residual:**
  - **Python plugins are not sandboxed.** They run in the proxy's process with
    its privileges and can read its environment, including provider keys. The
    import check is a lint: it catches mistakes and is trivially bypassed by code
    that means to.
  - Whoever holds `plugins:manage` (the `admin` and `operator` roles, and every
    admin key) can make the proxy load a Python file that is already under
    `plugins/` and passes the import check. The API does not upload files.
  - The alternative for untrusted code is a WASM plugin. It needs the `extism`
    package, which the published image does not include; without it a WASM plugin
    is loaded as a stub that does nothing.

---

## 4. OWASP LLM Top 10 (2025) mapping

Which control addresses which category. This is a mapping, not a measure of how
much is stopped; for that see the [benchmark](/security/benchmark).

| Category | Control | Limits |
|----------|---------|--------|
| **LLM01 — Prompt Injection** | Firewall and shield (L0, L3, L6), on by default; tool policy (L7), off by default. | The firewall and shield stop 21% of 3,035 attack prompts on the benchmark and 0% of 1,054 instructions planted in a tool result, and refuse 0.1% of 2,112 benign prompts. The tool policy is the control that addresses indirect injection. |
| **LLM02 — Sensitive Information Disclosure** | PII masking (L4). | A fixed set of patterns, chat messages only; see §3.4. |
| **LLM05 — Improper Output Handling** | Response sanitisation of non-streaming responses: invisible characters, links to block-listed domains. | HTML and script in a response are passed through. Streamed responses are not sanitised. |
| **LLM06 — Excessive Agency** | Tool policy (L7), off by default. | Limits which tool calls reach the caller; it does not see what the caller's agent does with them. |
| **LLM07 — System Prompt Leakage** | Shield patterns for extraction phrasings; response check for a short list of leak markers in non-streaming responses. | Lexical, as for LLM01. Streamed responses are not sanitised. |
| **LLM10 — Unbounded Consumption** | Body size limit, admission control, daily budget and per-key quota. | Rate limiting is off by default; no `max_tokens` cap by default; see §3.5. |

LLM03 (supply chain), LLM04 (data and model poisoning), LLM08 (vector and
embedding weaknesses) and LLM09 (misinformation) are not addressed by the proxy.

---

## 5. The regression corpus

`tests/corpus/owasp_llm_top10.yaml` is a set of attack and benign prompts written
together with the detector. `tests/test_owasp_corpus.py` runs each entry and
writes the result to [OWASP_LLM_COVERAGE.md](OWASP_LLM_COVERAGE.md). An entry
counts as blocked when one of these holds:

1. the firewall's signature scan matches, **or**
2. the shield's pattern score is at least `_HARD_BLOCK_SCORE` (0.85), **or**
3. the composite (`calculate_confidence`) decides "block".

The harness calls these functions directly. It does not send requests through
the running proxy, and it leaves out the session trajectory, the threat ledger,
the trigram comparison and the 0.5 rule the shield applies to the middle band.

Because the corpus was written with the detector, its pass rates show that a
change has not broken a case that used to be caught. They are **not** a detection
rate: on data the detector was not written against, the
[benchmark](/security/benchmark) measures 21% of attack prompts stopped.

---

## 6. Residual risks

- **Detection is lexical and partial.** See §3.2 and the
  [benchmark](/security/benchmark). The control that does not depend on
  recognising an attack is the tool policy, which is off by default.
- **Plugins are trusted code.** Python plugins are not isolated from the proxy;
  see §3.6.
- **API keys are the default credential.** OIDC is optional and off by default.
  Without `LLM_PROXY_ADMIN_KEYS`, inference keys administer the proxy.
- **Off by default:** rate limiting, response signing, the tool policy, OIDC.
- **Not provided:** mutual TLS; shared state across replicas for the shield's
  session memory, the threat ledger and the budget; an independent security
  review.

To re-run the regression corpus: `pytest tests/test_owasp_corpus.py -v`. To
reproduce the benchmark: `make waf-bench` (see
[Detection benchmark](/security/benchmark#reproducing-it)).
