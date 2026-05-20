# Spec — MCP-Native Gateway

| Status | Author | Created   | Target  | Owners      |
| ------ | ------ | --------- | ------- | ----------- |
| DRAFT  | fab    | 2026-05-20 | 1.22.0 | @fab (solo) |

---

## TL;DR

Turn LLMProxy into the **first open-source AI gateway with first-class
support for the Model Context Protocol (MCP)** — Anthropic's emerging
standard for connecting LLM clients to external tools, resources, and
prompts.

Concretely, in 1.22 LLMProxy will:

1. **Bridge** — expose configured MCP servers as standard OpenAI
   `tools` in `/v1/chat/completions`, so any existing OpenAI-compatible
   client (Cline, Cursor, Continue.dev, raw SDK, …) gets MCP-powered
   tool calls without modification.
2. **Proxy** — sit between an MCP client and one or more MCP servers,
   applying the same defense-in-depth (auth, audit, threat ledger,
   budget, PII masking) that already runs on chat traffic.
3. **Expose** — publish a native MCP server endpoint so MCP-aware
   clients (Claude Desktop, custom agents) can hook directly into
   LLMProxy's resources (audit log, threat events, registry state) and
   tools (panic kill-switch, plugin hot-swap, key rotation).

The three layers stack: a single config file declares the MCP fleet,
the bridge surfaces them everywhere, the proxy enforces policy on every
byte, and the native server lets MCP clients drive the gateway itself.

No competitor — LiteLLM, Portkey, Helicone, OpenRouter, Kong, Cloudflare
AI Gateway — has all three. Most have none. This is the wedge.

---

## Problem

MCP is the first standard for LLM ↔ tool plumbing that doesn't lock you
into a single vendor's agent framework (LangChain, LlamaIndex, OpenAI
Assistants). It's already shipped in Claude Desktop, Cursor, and a
growing list of editor plugins. Public registries like
[mcpservers.org](https://mcpservers.org) list hundreds of servers.

Three problems block production adoption today:

1. **Client lock-in.** Most MCP clients are desktop apps or IDE plugins.
   Anything driven by a regular OpenAI SDK (CI agents, batch pipelines,
   in-house apps) can't see MCP tools without writing a custom MCP
   client per service.
2. **No safety perimeter.** MCP servers can return arbitrary text,
   execute arbitrary tools, and read arbitrary resources. There is no
   audit trail, no PII scrubbing, no budget control, no rate limit.
   When the MCP server runs filesystem or git or shell tools, this is
   a footgun.
3. **No observability.** MCP traffic happens in the editor, locally,
   over stdio. There is no central place to ask "which tool was called
   in the last hour, by which key, on which session, with what
   arguments, returning what, for how much cost?". For a team that
   already adopted MCP, this is a real production gap.

LLMProxy already solves problems 2 and 3 for `/v1/chat/completions`.
Solving them for MCP is a straight extension of the existing ring
pipeline. Problem 1 is the marketing wedge — a single config bridges
the entire MCP ecosystem into every OpenAI-compatible client overnight.

---

## Goals

**G1.** Any MCP server (stdio or HTTP) declared in `config.yaml`
appears as a standard OpenAI `tool` in
`/v1/chat/completions` responses for clients that opt in.

**G2.** Tool calls invoked by the LLM and matching a configured MCP
server are routed through the proxy to the MCP server, the result
materialised back into the OpenAI response.

**G3.** Every MCP tool invocation goes through the same five rings as
chat traffic — audit-logged, threat-scored, PII-scrubbed, budget-
charged, plugin-extensible. No tool I/O escapes the perimeter.

**G4.** LLMProxy exposes an MCP server endpoint (SSE transport) so
MCP-aware clients can introspect and drive the gateway itself —
resources (audit log, registry, threats) read-only, tools (panic,
plugin hot-swap, key rotation) RBAC-gated.

**G5.** Configuration is declarative, hot-reloadable, observable in
the UI, and follows the same `.env` + `config.yaml` pattern as
existing endpoints.

---

## Non-goals (1.22)

- **No client-side MCP runtime.** We do not ship an MCP client library
  for app developers — they use the OpenAI SDK they already have.
- **No registry / marketplace.** No "browse and install MCP servers
  from the UI" in 1.22. Operators declare them in config; UI shows the
  declared list. A community catalog can be Phase 4.
- **No automatic security review of third-party MCP servers.** The
  threat ledger, semantic analyzer, and PII scrubber run on
  tool I/O, but we do not auto-quarantine new servers based on
  static analysis of their code. Operators opt in per server.
- **No JSON-RPC-over-stdio gateway hosting** — i.e. we do not let
  external clients tunnel arbitrary stdio MCP through LLMProxy. Only
  HTTP/SSE transport is exposed externally; stdio MCP servers we
  configure for the bridge are spawned and contained on the proxy
  host.
- **No multi-tenant MCP server isolation.** Each MCP server has one
  set of credentials; tenant-scoped access controls land in 1.23+ if
  there is demand.

---

## Background — MCP in 90 seconds

MCP (Model Context Protocol) is a JSON-RPC 2.0 protocol with three
primitive concepts:

| Concept    | What it is                                        | Example                             |
| ---------- | ------------------------------------------------- | ----------------------------------- |
| Resource   | Read-only addressable data (`uri`, `mimeType`)    | `file:///repo/CHANGELOG.md`         |
| Tool       | Callable function with JSON-Schema input          | `git_blame(path, line)`             |
| Prompt     | Reusable prompt template, parameterised           | `code-review(diff)`                 |

Two transports:

- **stdio** — server is a subprocess; client writes JSON-RPC to stdin,
  reads from stdout. The default for local servers (filesystem, git,
  shell, browser).
- **SSE/HTTP** — server listens on a URL; client sends JSON-RPC via
  POST, receives async messages via EventSource. The default for
  remote / hosted servers.

A standard MCP session is: `initialize` → `tools/list` →
(`tools/call` × N) → `shutdown`. Notifications can come asynchronously
from server to client (e.g. resource changed).

Spec: https://modelcontextprotocol.io/specification

---

## Architecture

Three components, layered.

```
                              ┌─────────────────────────────────────┐
                              │ /v1/chat/completions  (OpenAI API)  │
                              │ /v1/completions       (legacy)      │
                              │ /mcp                  (MCP server)  │ ← C
                              └────────────┬────────────────────────┘
                                           │
                              ┌────────────▼────────────────────────┐
                              │ Ring 1 ingress     auth/RBAC        │
                              │ Ring 2 pre-flight  PII/budget/cache │
                              │ Ring 3 routing     smart_router     │
                              │ Ring 4 post-flight quality/schema   │
                              │ Ring 5 background  audit/telemetry  │
                              └────────────┬────────────────────────┘
                ┌──────────────────────────┼──────────────────────────┐
                │                          │                          │
       ┌────────▼────────┐       ┌─────────▼────────┐       ┌─────────▼────────┐
       │ LLM forwarder    │       │ MCP bridge       │ ← A   │ MCP server       │ ← C
       │ (existing)       │       │  - register      │       │  (this proxy is  │
       │                  │       │  - tools/list    │       │   the SERVER)    │
       │ openai / anthr.. │       │  - tools/call    │       │                  │
       └────────┬────────┘       └─────────┬────────┘       └─────────┬────────┘
                │                          │                          │
                ▼                          ▼                          ▼
       upstream LLM             configured MCP servers       MCP clients
       providers                (stdio or SSE/HTTP)          (Claude Desktop,
                                                              custom agents)
                                                                  ▲
                                                                  │
                                                          (drives LLMProxy
                                                           itself via MCP)
                              ┌──────────────────────────────────────────────┐
                              │ Component B — MCP proxy mode                 │
                              │ (independent: a generic MCP-MITM that other  │
                              │ MCP clients can use to talk to MCP servers,  │
                              │ inheriting all 5 rings.)                     │
                              └──────────────────────────────────────────────┘
```

### Component A — MCP → OpenAI tool bridge

The marketing wedge. Drop-in OpenAI clients get MCP for free.

**Wire-level behaviour:**

1. Operator declares MCP servers in `config.yaml` (or
   `LLM_PROXY_MCP_*` env vars).
2. On boot, the proxy spawns / connects to each declared MCP server,
   calls `initialize`, then `tools/list`. Tool definitions are cached
   per server with TTL + `tools/list_changed` notification refresh.
3. When `/v1/chat/completions` is called with `tools: [...]` or
   `tool_choice: "auto"`, the proxy **prepends** the MCP-derived
   tools to the `tools` array passed upstream. Each MCP tool is
   namespaced: `mcp__<server>__<tool>` (e.g. `mcp__git__blame`).
4. If the LLM returns a `tool_calls` for a `mcp__*` name, the
   forwarder pauses, invokes the MCP server's `tools/call`, runs the
   response through ring 4 (PII / quality / schema), and re-enters
   the chat loop with the tool result. The client never sees the
   round-trip — it sees a normal OpenAI tool message.

**Opt-in:** off by default per request. Clients enable via
`extra_body: {"mcp_bridge": true}` (OpenAI SDK passes through), an
`X-LLMProxy-MCP: 1` header, or operator config `mcp.bridge.default_on:
true`. Tools NOT declared in MCP config are passed through unchanged.

**Failure modes:** if an MCP server errors during `tools/call`, the
proxy returns the JSON-RPC error as the tool message body so the LLM
can recover. If the server is unreachable, the proxy short-circuits
the call with a `{"error": "MCP server unreachable", "server": "<id>"}`
tool message and increments `llm_proxy_mcp_call_failures_total{server,
reason}`.

### Component B — Generic MCP proxy/MITM

For MCP-native clients (Claude Desktop, agent frameworks) that already
speak MCP and want defense-in-depth without giving up the protocol.

**Wire-level behaviour:**

1. Operator declares an MCP server in config with `proxy_upstream:
   true`.
2. The proxy listens at `/mcp/upstream/<server-id>` (SSE transport)
   and forwards every JSON-RPC request to the upstream MCP server.
3. Each `tools/call`, `resources/read`, `prompts/get` request goes
   through rings 1-5 before forwarding. Each response goes through
   ring 4 (PII, quality, schema). Audit log captures the full
   request/response pair (model: `<mcp-server>:<tool>`).
4. The proxy can be configured to require a Bearer key on the MCP
   endpoint and to enforce per-key allowlists of tools and resources.

Use case: a security-sensitive team gives developers an MCP-aware
editor but wants every tool call logged, every resource access
permission-checked, every PII string masked.

### Component C — LLMProxy as MCP server

For MCP-aware clients (Claude Desktop, custom agents) that want to
introspect or drive the gateway itself.

**Endpoint:** `GET /mcp` (SSE transport, OAuth-discoverable via
`/.well-known/mcp-server`).

**Resources (read-only):**

| URI                                    | What                                          |
| -------------------------------------- | --------------------------------------------- |
| `llmproxy://audit/recent`              | Last 100 audit entries, JSON                  |
| `llmproxy://audit/chain`               | Full hash-chained audit log                   |
| `llmproxy://registry/endpoints`        | Endpoint pool state                           |
| `llmproxy://threats/recent`            | Last 50 threat-ledger entries                 |
| `llmproxy://spend/today`               | Today's spend breakdown by model              |
| `llmproxy://plugins/loaded`            | Plugins per ring                              |
| `llmproxy://config/effective`          | Sanitised live config (no secrets)            |

**Tools (RBAC-gated):**

| Tool                       | What                                | Role required |
| -------------------------- | ----------------------------------- | ------------- |
| `panic`                    | Toggle the kill switch              | admin         |
| `plugin_hot_swap`          | Hot-swap a plugin                   | admin         |
| `endpoint_set_priority`    | Promote/demote an endpoint          | admin         |
| `rotate_api_key`           | Mint+invalidate an API key          | admin         |
| `feature_toggle`           | Flip a security guard               | admin         |
| `verify_audit_chain`       | Re-verify the hash chain            | user          |
| `query_audit`              | Query audit log with filters        | user          |

**Prompts:** none in 1.22 (the proxy doesn't host prompts; this is
where Phase 4 could expose a curated prompt library).

---

## Configuration model

`config.yaml`:

```yaml
mcp:
  enabled: true
  bridge:
    default_on: false           # bridge per-request opt-in by default
    namespace: "mcp__"          # tool-name prefix
    tools_ttl_s: 300            # tools/list cache TTL
  server:                       # Component C — LLMProxy AS an MCP server
    enabled: true
    base_path: /mcp
    auth: bearer                # bearer | none (auth disabled = dev only)
    rbac:
      tools_require_role: admin # except verify_audit_chain / query_audit (user)
  upstream:
    - id: git
      transport: stdio
      command: ["npx", "-y", "@modelcontextprotocol/server-git",
                "--repository", "/workspace"]
      bridge: true
      proxy_upstream: false
    - id: filesystem
      transport: stdio
      command: ["npx", "-y", "@modelcontextprotocol/server-filesystem",
                "/srv/safe"]
      bridge: true
      proxy_upstream: true     # also exposed at /mcp/upstream/filesystem
      allowed_tools: ["read_file", "list_directory"]
      denied_tools: ["write_file", "delete"]
    - id: github
      transport: http
      url: https://mcp.github.example/sse
      key_env: GITHUB_MCP_TOKEN
      bridge: true
      tools_allowlist:
        - "list_pull_requests"
        - "get_issue"
```

`.env` parity (no YAML editing):

```bash
LLM_PROXY_MCP_ENABLED=1
LLM_PROXY_MCP_SERVER_GIT_TRANSPORT=stdio
LLM_PROXY_MCP_SERVER_GIT_COMMAND='npx -y @modelcontextprotocol/server-git --repository /workspace'
LLM_PROXY_MCP_SERVER_GIT_BRIDGE=1
```

Hot-reload: changes to the `mcp:` block trigger re-init of changed
servers only — running stdio subprocesses for unchanged servers
survive, removed ones are SIGTERMed cleanly.

---

## API surface — what changes for clients

### Existing OpenAI clients (unchanged binary)

```python
client = OpenAI(base_url="http://llmproxy:8090/v1", api_key=KEY)
resp = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "What changed in CHANGELOG.md?"}],
    extra_body={"mcp_bridge": True},   # ← the only new line
)
# resp may carry tool_calls for mcp__git__log, which the proxy resolves
# transparently before returning. The final assistant message contains the
# answer; the tool trace is in resp.tool_calls if present.
```

### MCP-native clients (Claude Desktop, custom agents)

```jsonc
// claude_desktop_config.json
{
  "mcpServers": {
    "llmproxy": {
      "url": "https://gateway.example/mcp",
      "headers": { "Authorization": "Bearer sk-proxy-..." }
    }
  }
}
```

After connecting, Claude Desktop sees:

- 7 resources (audit/recent, registry/endpoints, threats/recent, …)
- 5 tools (panic, plugin_hot_swap, verify_audit_chain, …, role-gated)
- 0 prompts

### MCP MITM (component B)

A team that already runs Claude Desktop pointing at an external
filesystem MCP server reconfigures it to point at LLMProxy:

```jsonc
{
  "mcpServers": {
    "filesystem": {
      "url": "https://gateway.example/mcp/upstream/filesystem",
      "headers": { "Authorization": "Bearer sk-proxy-..." }
    }
  }
}
```

Every tool call now hits ring 1 → 5 before reaching the actual
filesystem MCP server.

---

## Security model

### Auth & secrets

- All MCP endpoints (`/mcp`, `/mcp/upstream/*`) sit behind the existing
  global auth middleware. No anon access in production.
- MCP server credentials (e.g. `GITHUB_MCP_TOKEN`) flow through the
  existing `core/secrets.py` SecretManager (Fernet-encrypted at rest,
  derived from `LLM_PROXY_MASTER_KEY`).
- The bridge never echoes upstream MCP credentials into responses. If
  an MCP server's `tools/call` returns text containing one of the
  configured secrets, the PII scrubber pattern set picks it up.

### Sandboxing of stdio servers

- stdio MCP servers run as subprocesses with:
  - dropped capabilities (Linux: `cap-drop=ALL` if Docker;
    `unshare`+`--mount-proc` if bare Linux);
  - read-only root FS except `/tmp` and explicit operator-mounted dirs;
  - a process group so the proxy can SIGKILL the whole tree on
    hot-reload removal;
  - a soft 30 MB RSS limit and a hard 60 MB cap;
  - no network egress unless `network: true` in the server config.
- For the MVP (Phase 1), sandboxing is a config flag (`sandbox: strict
  | none`) that requires Linux + Docker. macOS dev workflow disables
  it with a warning. Phase 2 introduces a `wasm:` transport that runs
  the MCP server in the existing `core/wasm_runner.py` for true
  cross-platform isolation.

### Ring integration

| Ring        | What runs on MCP tool I/O                              |
| ----------- | ------------------------------------------------------ |
| 1 ingress   | Bearer auth, RBAC for `/mcp` and `/mcp/upstream/*`     |
| 2 pre-flight | `tools/call` args: PII mask, complexity score, budget |
| 3 routing   | (no-op for MCP — there is no model to choose)          |
| 4 post-flight | tool result body: PII mask, schema validate, threat ledger |
| 5 background | audit log entry per call (req_id, tool, args_hash, cost_ms, status) |

### Threat ledger

Each MCP server gets a synthetic provider id `mcp:<server>`. Tool
calls increment the same threat-trajectory counters as model calls.
A server emitting consistent prompt-injection-shaped output (`ignore
previous instructions`, etc.) lights up the ledger and can be
auto-blocked by the existing `ZeroTrust` plugin.

---

## Observability

### Metrics (Prometheus)

```
llm_proxy_mcp_calls_total{server, tool, outcome}    counter
llm_proxy_mcp_call_latency_seconds{server, tool}    histogram
llm_proxy_mcp_call_failures_total{server, reason}   counter
llm_proxy_mcp_bridge_tools_advertised{server}       gauge
llm_proxy_mcp_subprocess_restarts_total{server}     counter
```

### Audit log shape

Reuse the existing `audit_log` table; `provider = "mcp:<server>"`,
`model = "<tool>"`, `metadata = {"args_sha256": "<hex>",
"transport": "stdio|sse"}`. Already hash-chained. Already verified
by `/api/v1/audit/verify`.

### UI

- New navigation entry **"MCP"** (after "Plugins").
- Live list of configured servers with status (connected /
  reconnecting / dead), advertised tool count, last error.
- Per-server drilldown drawer: tools list, recent calls (audit
  query), latency P50/P95, failure breakdown.
- Health-check button per server.

### Live debug

`POST /api/v1/mcp/<server>/tools/list` — admin-only, re-runs the
discovery against a server without restart. Useful when iterating
on a stdio server config.

---

## Phased rollout

### Phase 1 — Bridge MVP (1.22.0)

In scope:
- Configuration model + boot-time MCP server registration
- `tools/list` discovery + cache + advertisement in `/v1/chat/completions`
- `tools/call` round-trip + ring 2/4/5 integration
- stdio + HTTP/SSE transport
- New Prometheus counters
- 8 unit tests + 3 e2e tests
- UI: read-only MCP server list (no drilldown yet)
- 3 reference MCP servers tested: git, filesystem (read-only), github

Out of scope (Phase 1):
- LLMProxy AS MCP server (Component C)
- MCP MITM (Component B)
- Sandboxing beyond `cap-drop=ALL`
- UI drilldown
- Hot-reload of MCP config

### Phase 2 — Hardening + MITM (1.23.0)

- Component B (MITM proxy mode)
- Hot-reload + `tools/list_changed` notifications
- Subprocess sandboxing (Docker mode)
- UI drilldown drawer
- Audit chain verification for MCP entries

### Phase 3 — LLMProxy AS MCP server (1.24.0)

- Component C with the 7 resources / 5 tools above
- OAuth `/.well-known/mcp-server` discovery
- Claude Desktop integration walkthrough in `/docs/guide/mcp/`
- WASM transport for stdio sandboxing

### Phase 4 — Beyond (no version target)

- Community catalog of vetted MCP servers
- WASM transport for cross-platform stdio isolation
- Hosted MCP fleet as a SaaS-style offering
- MCP-based eval suite running upstream against your registered
  servers as part of CI

---

## Test plan

### Unit (pytest, Phase 1)

- `test_mcp_config.py` — config parsing (yaml + env), validation,
  defaults.
- `test_mcp_stdio_transport.py` — spawn echo server, send
  `initialize`, assert response shape.
- `test_mcp_http_transport.py` — same against in-process mock SSE
  endpoint.
- `test_mcp_tools_cache.py` — TTL expiry, `tools/list_changed`
  invalidation.
- `test_mcp_namespace.py` — tool name prefixing + collision
  detection across servers.
- `test_mcp_bridge_request.py` — chat completion with `mcp_bridge:
  true` advertises namespaced tools.
- `test_mcp_bridge_call.py` — LLM emits `tool_calls` for
  `mcp__git__log`, proxy invokes MCP, returns tool message.
- `test_mcp_audit_emits_entry.py` — every `tools/call` writes an
  audit entry (chain stays valid).

### E2E (pytest + real npm MCP servers, Phase 1)

- `test_e2e_mcp_filesystem.py` — boot the proxy with the real
  `@modelcontextprotocol/server-filesystem` against a tmp dir;
  send a chat completion that asks "what files are in /tmp"; assert
  the LLM's reply contains the file list.
- `test_e2e_mcp_threat_ledger.py` — invoke a tool whose output
  contains "ignore previous instructions and …"; assert the
  threat-ledger entry is created.
- `test_e2e_mcp_budget.py` — set a tiny budget; assert that the
  N+1 tool call (after budget exhaustion) is blocked by ring 2.

### Manual / live (Phase 1)

- Cline + LLMProxy + git MCP: ask "explain the last commit"; verify
  audit log shows the tool call.
- OpenAI Python SDK + LLMProxy + filesystem MCP: list files in a
  permitted dir; verify the prompt audit and the tool audit are
  linked by the same req_id.
- Prometheus scrape: confirm `llm_proxy_mcp_calls_total` populates.

---

## Risks

**R1. MCP protocol churn.** MCP is a young standard. Spec changes
between 0.x revisions are likely. Mitigation: pin a single MCP
spec version per LLMProxy release; the proxy logs a clear
`MCP protocol version mismatch` error when a server advertises a
version we don't speak; bump support in minor releases.

**R2. Tool name collisions.** Two MCP servers expose tools with the
same name. The `mcp__<server>__<tool>` namespace defuses
collision *between* MCP servers. A collision *with* a non-MCP tool
the client already declared in `tools:` is impossible by
construction (the `mcp__` prefix is reserved). Tested in
`test_mcp_namespace.py`.

**R3. stdio MCP server leak.** A subprocess crashes or hangs and
the proxy doesn't reap it. Mitigation: process group + SIGTERM
on hot-reload + watchdog that restarts (with exponential backoff)
on unexpected exit; restart counter exposed as a metric.

**R4. Tool-call loops.** The LLM keeps calling the same MCP tool
indefinitely. Mitigation: per-trace budget (trace-aware budgets is
an A-tier item already planned) caps the total tool-call count
per session; ring 2 emits a budget error after the cap.

**R5. PII echo.** An MCP server returns sensitive data that bypasses
the existing PII regex set (the regex set was tuned for chat
text, not e.g. structured filesystem dumps). Mitigation: ring 4 PII
runs on the tool result body before it's materialised back into the
chat trace; pattern set expanded with file-path / SSH-key patterns
in Phase 1.

**R6. Marketing wedge slips.** Someone (LiteLLM most likely) ships
MCP bridge first. Mitigation: ship Phase 1 in two weeks; do not
let scope creep delay it. The MITM and "LLMProxy as MCP server"
components can come later — what wins HN is the
"any OpenAI client now has MCP for free" claim.

---

## Open questions

- **Q1.** What is the right opt-in surface? `extra_body:
  {"mcp_bridge": true}` is OpenAI-SDK-friendly, but a header
  (`X-LLMProxy-MCP: 1`) is friendlier to raw curl. Ship both?
- **Q2.** Should the proxy run an in-process MCP client library, or
  shell out to the official Anthropic Python `mcp` package?
  In-process is faster but pulls a heavier dep. **Lean: official
  package**, hidden behind `proxy/mcp/client.py` adapter so we can
  swap later.
- **Q3.** Do we expose the MCP fleet as one aggregate tool surface
  per request (all 30 tools across 6 servers), or filter by
  client-declared interest? MVP says aggregate. If the LLM context
  bloats, add a request-level `mcp_servers: ["git", "filesystem"]`
  filter.
- **Q4.** For component C (LLMProxy AS MCP server), is OAuth
  worth the cost in 1.24, or do we ship Bearer-only and let
  operators front it with Caddy/Pomerium for OAuth?
- **Q5.** Do `mcp__*` tools count toward the per-request token
  budget when we advertise them? The schemas can be large. Phase
  1: yes, the operator can cap with `mcp.bridge.max_advertised:
  N` (default 50). Phase 2: smart shortlist via semantic match
  against the prompt.

---

## Appendix A — Example end-to-end trace

Client (Python OpenAI SDK):
```python
resp = client.chat.completions.create(
    model="gpt-4o",
    messages=[
        {"role": "user", "content": "What's in the latest commit on main?"}
    ],
    extra_body={"mcp_bridge": True},
)
```

Wire trace:
1. POST `/v1/chat/completions` arrives at LLMProxy.
2. Ring 1: bearer ok, RBAC `proxy:use` ok.
3. Pre-flight: MCP bridge enabled by request flag. Pre-cached tool list
   from `git` server is prepended to `tools` array.
4. Forwarder sends to gpt-4o with `tools: [mcp__git__log, mcp__git__diff, …]`.
5. gpt-4o responds with `tool_calls: [{name: "mcp__git__log",
   arguments: {"max_count": 1}}]`.
6. Forwarder detects `mcp__` prefix, invokes `tools/call` on the
   local `git` MCP subprocess.
7. Result: `[{"hash": "ae18f47", "message": "…"}]`.
8. Ring 4 runs PII / quality / schema on the tool result.
9. Ring 5 writes audit entry: `provider=mcp:git`, `model=log`,
   `req_id=…`, `args_sha256=…`, `latency_ms=12`.
10. Forwarder re-sends to gpt-4o with the tool message appended.
11. gpt-4o produces the final assistant turn ("The latest commit
    is ae18f47, …").
12. Ring 4 + 5 on the chat response (existing path).
13. Response returned to client. The full tool call appears in
    `resp.tool_calls`; the answer is in `resp.choices[0].message`.

Audit chain after the request:
- entry N+1: `provider=openai`, `model=gpt-4o`, `prompt_tokens=…`
- entry N+2: `provider=mcp:git`, `model=log`, `latency_ms=12`
- entry N+3: `provider=openai`, `model=gpt-4o`, `prompt_tokens=…+tool`

Both entries share the same `req_id` so a drilldown shows the full
trace in one drawer.

---

## Appendix B — What we ship as proof on Phase 1 launch day

Blog post draft: "We made every OpenAI client MCP-native in one
config line."

Benchmark + screenshots:
- Cline + LLMProxy + git MCP → "explain the diff in HEAD" works
  out of the box with `extra_body: {mcp_bridge: True}`.
- `curl` request to `/v1/chat/completions` returns tool calls and
  resolves them transparently.
- Prometheus dashboard screenshot showing `llm_proxy_mcp_calls_total`
  rising under load.
- A "before / after" of the audit log showing tool calls appearing
  with full traceability.

Hacker News title: "Show HN: LLMProxy 1.22 — every OpenAI client now
speaks MCP, with audit + budget + threat ledger on every tool call"

Distribution checklist:
- [ ] HN Show post (Tuesday 9am PT for max visibility)
- [ ] Twitter / X thread with screenshots
- [ ] LinkedIn post targeting CISO + platform-eng audience
- [ ] DM to LiteLLM / Helicone maintainers (cordial; "FYI we shipped
      this, happy to coordinate on protocol-level interop")
- [ ] Submit to the official Anthropic MCP registry list
- [ ] 90-second YouTube demo video

---

## Glossary

| Term         | Meaning                                                          |
| ------------ | ---------------------------------------------------------------- |
| MCP          | Model Context Protocol — JSON-RPC standard for LLM ↔ tools       |
| Bridge       | LLMProxy → other MCP servers (Component A)                       |
| MITM         | LLMProxy in front of an MCP server (Component B)                 |
| Server-mode  | LLMProxy AS an MCP server, surfacing its own state (Component C) |
| stdio        | Subprocess transport — JSON-RPC over stdin/stdout                |
| SSE          | Server-Sent Events — async server→client notifications over HTTP |
| Tool         | An invokable MCP function with JSON-Schema input                 |
| Resource     | A read-only MCP-addressable URI with optional MIME type          |
| Prompt       | A parameterised reusable prompt template                         |
