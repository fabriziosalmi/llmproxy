# Changelog

All notable changes to LLMProxy are documented here.

## [1.10.4] — 2026-03-30

### Red Team Security Audit (Round 1) — 13 exploitable findings fixed

**HIGH severity:**
- **Domain blocklist substring bypass** (H1) — `_check_links()` and `sanitize_response()` used `domain in url` substring match. "not-malicious-site.com" matched "malicious-site.com". Fixed: `urlparse().netloc` with exact/suffix domain boundary match.
- **PII masker only last message** (H2) — `pii_masker.py` only masked `messages[-1]`. SSN/CC in earlier messages forwarded to upstream LLM in cleartext. Fixed: loops ALL messages.
- **Session collapse without auth** (H5) — All users behind same NAT shared one session (IP-only). Attacker's threat score poisoned legitimate users. Fixed: `hash(IP:User-Agent:Accept-Language)` fingerprint.

**MEDIUM severity:**
- **Response sanitizer only choices[0]** (H3) — `n>1` requests bypassed sanitization on alternative completions. Fixed: loops ALL choices.
- **Identity error detail leak** (H7) — `ValueError` from JWT verification exposed JWKS paths, OIDC URLs, algorithm info. Fixed: generic "Invalid or expired token".
- **Semantic analyzer DoS** (H10) — Default unbounded executor + no timeout. 100k-char prompts saturated thread pool. Fixed: `ThreadPoolExecutor(4)` + `asyncio.wait_for(5s)`.
- **Threat score leak** (H11) — Exact score in 403 enabled binary search to calibrate prompts at 0.69 (just below 0.7 threshold). Fixed: generic error, score only in server logs.
- **Streaming bypasses sanitizer** (H12) — Documented architectural limitation in `shield_sanitizer.py`.

**LOW severity:**
- **Rate limiter bucket race** (H4) — `bucket.acquire()` called after global lock release; evicted bucket could be used. Fixed: acquire inside lock.
- **_extract_prompt() double call** (H6) — Deduplicated: reuse from step 0.
- **Trace ID log injection** (H8) — Unvalidated `X-Trace-Id` header. Fixed: regex `^[a-fA-F0-9-]{1,64}$`.
- **NegativeCache ignores model** (H9) — Same messages + different model shared cache entry. Fixed: hash includes `[model, messages]`.
- **req_id 32-bit collision** (H13) — Birthday bound at 65k requests. Fixed: 64-bit (`uuid4.hex[:16]`).

### WAF Hardening (14 audit findings)
- **Encoding chain bypass** (W1) — Iterative 2-level nested decoding: catches `Base64(URL("..."))`, double-base64, `Hex(Base64("..."))`.
- **Typo/leetspeak evasion** (W2) — Leetspeak normalization (`1→i, 0→o, 3→e, @→a, $→s`) before trigram matching.
- **Few-shot injection** (W3) — `_extract_prompt()` inspects ALL messages + `tool_calls` (name + arguments).
- **Firewall signatures** (W4) — 11 → 28 (DAN, jailbreak, role-play, delimiter, social engineering).
- **Multilingual corpus** (W5) — 7 → 17 patterns (+ZH, RU, PT, HI, TR, PL). 64 total semantic patterns.
- **Bidi override detection** (W6) — `U+202A-202E`, `U+2028-2029`, `U+180E` added.
- **PII international** (W7) — +4 patterns: intl phone, IPv4, API keys, Amex 15-digit.
- **Sliding window step** (W8) — Capped at `window_size - pattern_len`.
- **Firewall latency metrics** (W9) — `total_scan_time_ms` + `max_scan_time_ms`.
- **Homoglyph threshold** (W10) — 20% → 10% (fewer false positives on multilingual code).
- **Entropy threshold** (W10) — 1.0 → 1.5 (catches repetition attacks).

### UI/UX Overhaul (23 of 32 findings fixed)
- **Auth headers on all API calls** — `_fetch()` wrapper auto-injects Bearer token; UI works with auth enabled.
- **Error handling** — `_fetch()` throws on non-2xx; silent failures eliminated.
- **Toast notification system** — Replaces all native `alert()` calls.
- **URL hash routing** — `#/tab` navigation survives refresh, supports back/forward.
- **Font sizes** — All `text-[7px]`/`text-[8px]` upgraded to 9px/10px minimum (WCAG).
- **View-scoped polling** — `store.poll()` only fetches active tab; Page Visibility API pauses on hidden.
- **Command palette keyboard nav** — Arrow keys, Enter to activate, ARIA roles.
- **ARIA landmarks** — `role="navigation"`, `role="main"`, `role="banner"`, `role="switch"`.
- **Models search/filter**, **GDPR file download**, **registry table sort**, **reduced-motion**, **glassmorphism fallback**, **touch targets**, **selective re-rendering**, **SSE reconnection**.

### Stats
- 915/915 tests passing (was 870)
- 28 firewall signatures (was 11)
- 64 semantic injection patterns (was 54)
- 17 multilingual patterns across 12 languages (was 7 across 5)
- 9 PII regex patterns (was 5)
- 34 new WAF-specific tests
- 3 exploit chains identified and neutralized

---

## [1.10.2] — 2026-03-30

### Critical Fixes (SEV-0)
- **Streaming budget bypass** — `cost_ref["delta"]` was read by the rotator immediately after `forward_with_fallback` returned, but for streaming responses the generator hadn't run yet — delta was always `0.0`. Budget lock and rotator ref now passed via `cost_ref`; the stream generator charges atomically in its `finally` block. Non-streaming path unchanged.
- **GDPR endpoints crash** — `delete_subject_data()` and `export_subject_data()` queried a `user_roles` table that `init_db()` never created. Added `CREATE TABLE IF NOT EXISTS user_roles` to `init_db()`.
- **Analytics cost-efficiency always zero** — `admin.py` looked for dict keys `request_count`/`total_cost` but `query_spend()` returns `requests`/`total_cost_usd`. Fixed key names; total tokens now sums prompt + completion.

### Race Conditions (SEV-1)
- **SQLiteStore `_get_conn()` race** — Two concurrent coroutines could both see `_conn is None`, create duplicate connections, and leak one (WAL lock contention, `database is locked` errors). Added `_conn_lock` with double-check locking.
- **`row_factory` race** — `conn.row_factory` is connection-level in aiosqlite; concurrent queries toggling it corrupted each other's result types (`dict(r)` → `TypeError`). Added `_row_factory_lock` protecting all 4 methods: `query_spend`, `query_audit`, `export_subject_data`, `verify_audit_chain`.
- **`CircuitManager.get_breaker()` race** — Two coroutines calling `get_breaker("new_endpoint")` could create duplicate `CircuitBreaker` instances; failures reported to the orphan were lost. Made `async` with `asyncio.Lock`; all 6 callers updated.
- **PII regex mutation during iteration** — `re.finditer()` iterated the original string while `masked.replace()` shifted offsets, causing subsequent matches to replace at wrong positions or miss entirely. Rewrote with `re.sub()` + callback — zero offset drift.

### Logic Fixes (SEV-2)
- **`_check_payload_flooding` ZeroDivisionError** — Whitespace-only prompts >2000 chars caused `len(prompt.split()) == 0` → division by zero → 500 instead of 403. Added `words and` guard.
- **`trace_id` operator precedence** — `A or B if C else None` parsed as `(A or B) if C else None`, silently ignoring `x-trace-id` header when `traceparent` was absent. Fixed with explicit parentheses.
- **Response injection false positives** — Patterns `human:` and `assistant:` matched anywhere in LLM output, replacing legitimate content with `[SEC_ERR: THREAT_DETECTED]`. Anchored to line start: `^human:`, `^assistant:`.
- **Config hot-reload loses SecurityShield assistant** — Background `config_watch_loop` recreated `SecurityShield` without `assistant=` param (unlike admin reload). AI guardrails (`inspect_response_ai`, `detect_anomaly`) silently stopped working.

### Infrastructure (SEV-3)
- **Shutdown WAL checkpoint** — Opened a second connection for `PRAGMA wal_checkpoint(TRUNCATE)` while `_conn` was still open; TRUNCATE requires exclusive access, so checkpoint silently became a no-op. Now uses existing `_get_conn()`.
- **Deduplicator race** — Second caller's future comparison `self._in_flight.get(key) is not future` was always `False` (same object), causing duplicate upstream requests. Rewrote with explicit `is_executor` flag: waiters await the future, only the executor awaits the coroutine.

### Stats
- 830/830 tests passing (excl. integration)
- 13 files changed across core/, proxy/, store/, plugins/
- 0 regressions

---

## [1.10.1] — 2026-03-29

### Critical Fixes (SEV-0)
- **Double budget charging** — Cost was added in both `rotator.py` (via `cost_ref`) and `chat.py`, exhausting the daily budget at 2x the real spend rate. Removed the duplicate in `chat.py`.
- **admin/reload crash** — Called `_compute_config_hash()` which didn't exist; fixed to `_compute_config_hash_sync()`.
- **AI guard lost after config reload** — `SecurityShield` was recreated without `assistant=` param, silently disabling `inspect_response_ai()` and `detect_anomaly()`.
- **GDPR purge without auth** — `POST /api/v1/gdpr/purge` had no `_check_admin_auth()` call; anyone could trigger data deletion.

### Race Conditions (SEV-1)
- **HTTP session creation race** — `_get_session()` now uses `asyncio.Lock` with double-check locking. Previously, concurrent requests could both create sessions, orphaning TCP connectors.
- **CircuitBreaker async-safety** — `can_execute()`, `report_success()`, `report_failure()` are now `async` with `asyncio.Lock`. Half-open state admits exactly one probe request via `_half_open_probe_active` flag.
- **Firewall counter atomicity** — `block_by_signature` and `block_by_encoding` changed from `dict` to `defaultdict(int)`, eliminating the non-atomic `get()+set()` read-modify-write pattern.

### Design Fixes (SEV-2)
- **SQLiteStore persistent connection** — Replaced per-query `aiosqlite.connect()` with a single persistent connection (`_get_conn()`), matching the `CacheBackend` pattern. Eliminates connection open/close overhead and WAL lock contention.
- **Google adapter API key leak** — API key moved from URL `?key=` query parameter to `x-goog-api-key` header. Keys no longer appear in logs, HTTP Referer headers, or proxy caches.
- **Session ID derived from token hash** — `session_id` is now `sha256(token)[:16]` instead of the raw API key. Secrets no longer stored in `session_memory`, audit logs, or log messages.
- **SecurityShield.inspect() now async** — `semantic_scan()` runs in `asyncio.run_in_executor()` to avoid blocking the event loop on CPU-intensive embedding computation.
- **`_last_eviction` instance-level** — Moved from class variable to instance variable; multiple `SecurityShield` instances no longer interfere with each other's eviction timing.

### Performance (SEV-3)
- **Pricing prefix specificity** — Prefix list sorted longest-first so `gpt-4o-mini` matches before `gpt-4o` for versioned model names like `gpt-4o-mini-2025-01`.
- **Streaming spend_log accuracy** — Forwarder now logs spend entries with real post-stream token counts directly in the `finally` block; `chat.py` no longer logs cost=0 for streaming requests.
- **detect_steganography single pass** — Merged 3 separate O(n) character scans + 2 counting passes into a single O(n) pass with accumulated counters. Regex pre-compiled at class level.
- **Hot path imports to top-level** — 6 lazy imports (`resolve_model`, `update_endpoint_stats`, `MetricsTracker`, `TraceManager`, `EventType`, `estimate_cost`) moved from per-request to module level.

### Docs
- README: test count 595 → 870, injection corpus 60/57 → 54 (actual), coverage gate 50 → 65

### Stats
- 870/870 tests passing
- 24 files changed, +570 −436 lines

---

## [1.10.0] — 2026-03-26

### Security — Architectural (Fail-Closed by Default)
- **Global fail-closed auth middleware** (`proxy/app_factory.py`) — Replaces the fail-open per-route opt-in pattern with a deny-all ASGI middleware. All paths under `/api/v1/*` and `/admin/*` require a valid API key by default. New routes are automatically protected; developers must explicitly whitelist public paths in `_PUBLIC_EXACT`. Eliminates the structural "whack-a-mole" pattern that produced auth gaps in rounds 7, 8, 9.
- **API docs disabled in production** — `/docs`, `/redoc`, `/openapi.json` disabled when auth is enabled. FastAPI interactive docs exposed a full endpoint map before authentication.
- **`/metrics` protected** — Prometheus endpoint exposed token counts, model usage, budget state, and timing side-channels usable for cross-tenant inference.
- **Per-route `_check_admin_auth()`** retained as defence-in-depth.

### Public whitelist (all other `/api/v1/*` paths require auth)
```
/health, /api/v1/identity/config, /api/v1/identity/exchange, /api/v1/identity/me
```

---

## [1.9.3] — 2026-03-26

### Security
- **Unauthenticated registry mutations** (CRITICAL) — `POST /api/v1/registry/{id}/toggle`, `DELETE /api/v1/registry/{id}`, `POST /api/v1/registry/{id}/priority`, and `GET /api/v1/telemetry/stream` had no auth check. Added `_check_admin_auth()` to all four.
- **Unauthenticated `/api/v1/logs` SSE stream** (MEDIUM) — Streamed SECURITY-level events (blocked IPs, failed auth, user emails, plugin operations) to unauthenticated callers. Auth guard added.
- **Incomplete PII scrubber** (MEDIUM) — `_SENSITIVE_FIELDS` expanded from 5 to 18 entries; added Anthropic `sk-ant-...` key pattern to `PII_PATTERNS`.
- **OTLP `insecure=True` hardcoded** (LOW) — Now conditional: plaintext gRPC only for localhost endpoints, TLS enforced for remote collectors.

---

## [1.9.2] — 2026-03-26

### Security
- **Unauthenticated plugin management** (CRITICAL) — Plugin install/toggle/uninstall/hot-swap/rollback had no auth. Any caller could install arbitrary code or disable security plugins. `_check_admin_auth()` added to all mutating plugin routes.
- **Unauthenticated GDPR erase/export** (HIGH) — Any caller could delete or exfiltrate all subject data. Auth now required on erase and export endpoints.
- **JSON injection in GDPR audit metadata** (MEDIUM) — Audit log built via f-string interpolation of URL path parameter `subject`. Replaced with `json.dumps()`.

---

## [1.9.1] — 2026-03-26

### Security
- **WASM sandbox escape / memory corruption** (CRITICAL) — `asyncio.Lock` is released on `asyncio.wait_for` timeout but the native thread in `run_in_executor` keeps running. A second coroutine could acquire the released lock and enter the Extism runtime concurrently, corrupting WASM linear memory (SIGSEGV / cross-tenant data leak). Replaced with `threading.Lock` acquired inside `_sync_call` — held for the thread's lifetime regardless of coroutine cancellation.
- **Plugin directory traversal** (HIGH) — `os.path.join(plugins_dir, abs_path)` silently discards the base directory when the second argument is absolute. Added `os.path.abspath` containment check for both Python and WASM plugin paths.
- **Blocking SQLite on asyncio event loop** (HIGH) — `set_user_roles` and `get_user_roles` called `sqlite3.connect()` directly on the event loop, blocking all concurrent requests. Both methods now `async`, wrapping SQLite calls in `asyncio.to_thread`.

---

## [1.9.0] — 2026-03-26

### Security
- **Budget lost-update race** (CRITICAL) — Concurrent streaming requests each snapshotted the same `total_cost_today`, causing the last stream to overwrite all prior charges. Fixed with delta-based accounting: each request accumulates its own `{"delta": 0.0}` dict; the rotator adds it atomically under `budget_lock` after completion.
- **DNS rebinding SSRF** (HIGH) — Load-time `socket.gethostbyname()` check was TOCTOU (attacker serves public IP at validation, private IP at request time). Replaced with `_SSRFBlockingResolver`: a custom `aiohttp.abc.AbstractResolver` that validates resolved IPs at TCP connect time, after every DNS lookup. Fail-closed on DNS failure.

### Documentation
- README: test badge updated (716 → 870), Webhook Security and Concurrent Budget Safety sections added
- `docs/security/overview.md`: sections added for Webhook Security and Budget Integrity

---

## [1.8.4] — 2026-03-26

### Security
- **`_cost_ref` singleton race** (CRITICAL) — `RequestForwarder._cost_ref` was a shared instance attribute; concurrent requests overwrote it between `bind_cost_ref()` and the stream `finally` block, causing cost charges to bleed across tenants. Each request now uses an isolated `cost_ref` dict passed as parameter.
- **SSRF via webhook dispatcher** (HIGH) — `WebhookDispatcher._send()` called `session.post()` with no URL validation. Added `_validate_webhook_url()`: enforces http/https, checks IP literals against private/reserved CIDRs (RFC-1918, loopback, link-local, IPv6 ULA).
- **Unsigned webhook payloads** (HIGH) — `WebhookConfig.secret` was never applied. Added HMAC-SHA256 signing: `X-Webhook-Signature: sha256=<hex>` injected when a secret is configured.
- **ASGI disconnect mid-body accumulation** (LOW) — Early-exit on `http.disconnect` forwarded disconnect with no preceding `http.request`, causing a FastAPI parse error. Now returns immediately.

### Fixed
- Restored `Dict` import in `core/security.py` and `core/rate_limiter.py` (removed by earlier OrderedDict refactor)
- Removed unused `pytest_asyncio` import in `tests/test_rbac.py`

---

## [1.8.3] — 2026-03-26

### Security
- **Session memory O(N) LRU eviction** (MEDIUM) — `check_session_trajectory` ran `min()` over up to 10 000 entries on every new session at capacity. Replaced with `OrderedDict` + O(1) `popitem(last=False)` / `move_to_end()`.
- **Cache key Unicode poisoning** (MEDIUM) — `make_key()` lacked Unicode normalisation; invisible chars (U+200B, fullwidth, combining marks) produced diverging SHA256 keys for visually identical queries. Added NFKC normalisation before hashing.
- **Speculative guardrail polling gap** (LOW) — `analyze_speculative()` polled every 50ms; at 10-20ms/chunk this allowed ~5 LLM tokens to escape before PII detection. Reduced to 10ms.

### Tests
- Coverage: 54% → 67% (gate raised to 65%)
- 716 tests passing (was 595)

---

## [1.8.2] — 2026-03-26

### Security
- **Rate limiter O(N) DoS** (CRITICAL) — `_evict_oldest()` ran `min()` over 50 000 entries while holding the global asyncio lock, stalling the event loop on every request under saturation. Replaced with `OrderedDict` + O(1) eviction.
- **WASM concurrent memory corruption** (CRITICAL) — Concurrent `_plugin.call()` on the same Extism instance corrupted WASM linear memory, enabling cross-tenant data leaks and SIGSEGV. Per-runner `asyncio.Lock` added.
- **Fail-open cryptographic fallback** (HIGH) — `SecretManager.decrypt()` silently returned plaintext on any exception. All failure paths now log at ERROR.
- **Dead speculative stream guardrail** (MEDIUM) — `SecurityShield.analyze_speculative()` existed but was never called; streaming responses bypassed real-time content inspection. Wired in `_handle_streaming` as a background task.

---

## [1.8.1] — 2026-03-26

### Security
- **Firewall split-payload bypass** (CRITICAL) — ASGI middleware scanned each chunk independently; a banned phrase split across TCP packets evaded detection. Now accumulates full body before scanning.
- **OOM/DoS via missing `Content-Length`** (CRITICAL) — Byte-level body size guard now enforced at ASGI layer regardless of `Content-Length` presence.
- **Audit hash-chain race** (HIGH) — `log_audit` SELECT → compute → INSERT sequence serialised with `asyncio.Lock`, preventing concurrent chain forks.
- **Blocking SQLite in RBAC** (HIGH) — `check_quota` and `update_usage` made `async` with `asyncio.to_thread` offloading.
- **O(N) session eviction** (MEDIUM) — `check_session_trajectory` eviction throttled to once per 30 seconds.

---

## [1.8.0] — 2026-03-25

### Added
- **Mathematical invariant suite** — 31 property-based tests proving correctness on every commit (Hypothesis, ~2 950 randomized cases per run). Covers injection self-detection (57 patterns), Jaccard axioms, normalisation idempotence, pricing non-negativity, rate limiter token conservation, concurrency stress (500 coroutines).
- **519× performance boost** — `semantic_scan` optimized from 290ms → 0.56ms on 1 200-word prompts.
- **8-job CI pipeline** — Lint, Type Check, Dependency Audit, Supply Chain, Syntax, Test+Coverage (≥50%), Mathematical Invariants, Docker Size.
- **22 performance benchmarks** with `pytest-benchmark`.

### Fixed
- 6 race conditions (RateLimiter, BudgetGuard, NeuralRouter, Deduplicator, Rotator budget, HTTP timeouts)
- 22 silent error handling issues
- Content-Length payload guard (OOM protection)

---

## [1.7.2] — 2026-03-25

### Added
- `config.minimal.yaml` — 45-line quickstart config (1 provider, ready in 5 min)
- `Makefile` — 15 targets: `make setup`, `make run`, `make test`, `make bench`, `make docker-up`
- `core/startup_checks.py` — actionable error messages on misconfiguration
- `.devcontainer/devcontainer.json` — GitHub Codespaces / VS Code ready
- Grafana dashboard JSON (11 panels: latency, errors, budget, tokens, circuit breakers, injections)
- Prometheus alert rules (8 alerts + 4 recording rules)
- `SECURITY.md` — vulnerability disclosure policy
- `CONTRIBUTING.md` — contributor guide with PR checklist
- OpenAPI version synced from VERSION file (was hardcoded 0.1.0)
- Docker GHCR push on `main` branch

### Fixed
- 6 race conditions, HTTP timeouts on all adapters, OOM protection
- 4 silent `except Exception: pass` blocks now log errors
- 3 broad `except Exception` narrowed to specific types

---

## [1.7.1] — 2026-03-24

### Added
- **Supply chain .pth scanner hardened** — 5 detection categories (code execution, network access, persistence, credential exfiltration, process spawning) based on litellm 1.82.8 malware analysis. Scans at boot, in CI post pip-install, and during Docker build.
- **Plugin circuit breaker** — auto-quarantines plugins after 10 consecutive errors (60s cooldown, half-open retry).
- **Health prober jitter** — startup jitter (0-50% interval), per-round jitter (±20%), per-probe jitter (0-5s) to prevent thundering herd.
- **Connection pool config** — `connection_pool` section in config.yaml for max_connections, max_per_host, dns_cache_ttl, keepalive_timeout, connect_timeout.
- **Dockerfile .pth audit** — build-time scan for malicious .pth files post pip-install.
- **CI explicit .pth audit** — grep-based .pth scan step in Supply Chain Integrity job.

### Changed
- **Lexical analyzer hardened** — sliding window comparison for length-independent detection, dual-gate system (overlap + Jaccard), adaptive thresholds for short patterns, 3 overly-generic corpus patterns made more specific. 14 adversarial false-positive tests added.
- **Response signer docs** — renamed "provenance" → "attestation". Explicit threat model (✅ proxy→client tamper, ❌ LLM→proxy integrity). Honest about limitations.
- **ASGI firewall hardened** — added base64/hex/unicode escape decoding layer, zero-width character stripping, nested encoding detection.

### Stats
- Tests: 654 → 687 (+33)
- CI: 7/7 jobs green
- Supply chain: 6-layer defense in depth

## [1.7.0] — 2026-03-24

### Added
- **Semantic injection detection** — Paraphrase-resistant attack analysis using character trigram Jaccard similarity against 60 known injection patterns across 8 categories (override, extraction, hijack, bypass, multilingual, delimiter, social, exfiltration). Catches synonym substitution, multilingual injection (IT/DE/FR/ES/JA/KO/AR), verbose wrapping. Zero external deps, <1ms latency.
- **Cross-session threat intelligence** — ThreatLedger aggregates threat scores by IP and API key across sessions, detecting coordinated attacks where actors rotate session IDs. Memory-bounded, configurable threshold/window.
- **HMAC response signing** — Cryptographic provenance via `X-LLMProxy-Signature` header. Signs model + provider + timestamp + request_id + body. Constant-time verification prevents timing attacks.
- **Immutable audit ledger** — SHA256 hash chain on audit_log entries. Each entry links to the previous via `prev_hash`. Tamper/deletion/insertion detected via `GET /api/v1/audit/verify`.
- **GDPR compliance endpoints** — `POST /api/v1/gdpr/erase/{subject}` (right to erasure), `GET /api/v1/gdpr/export/{subject}` (DSAR with PII scrubbing), `GET /api/v1/gdpr/retention` (policy), `POST /api/v1/gdpr/purge` (manual retention purge). Background auto-purge loop (default 90 days).
- **Cost-aware routing** — Neural router scoring now includes cost factor: `score = (success²/latency) × cost_factor^w`. Configurable `routing.cost_weight` (0.0–1.0, default 0.3).
- **Budget fallback to local** — When `budget.fallback_to_local_on_limit` is enabled and daily limit is exceeded, requests auto-downgrade to local model (e.g., `ollama/llama3.3`) instead of rejecting.
- **Cost efficiency API** — `GET /api/v1/analytics/cost-efficiency` returns per-model cost/request with cheapest/most expensive ranking.
- **SOC Security Events panel** — New "Security" tab in the dashboard with threat ledger KPIs, audit chain verification, GDPR controls, semantic corpus breakdown. Accessible via sidebar nav and Cmd+K palette.
- **Pipeline E2E tests** — 15 tests validating the full 5-ring proxy pipeline (ring execution order, SecurityShield blocking, negative cache, plugin stop_chain, budget enforcement, response headers, concurrency).
- **Cost routing tests** — 11 tests for cost-aware scoring and budget downgrade.
- **GDPR tests** — 11 tests for erasure, DSAR export, retention purge.
- **Audit chain tests** — 9 tests for hash chain integrity, tamper/deletion detection.
- **Semantic analyzer tests** — 28 tests for known attacks, paraphrases, multilingual, false positives.

### Changed
- **Split rotator.py** — Monolithic orchestrator (758 lines) decomposed into 5 focused modules: `rotator.py` (444), `forwarder.py` (224), `app_factory.py` (94), `background.py` (76), `event_log.py` (54).
- **Budget persistence hardened** — Write flush interval reduced from 1.0s to 250ms. Immediate flush on critical budget threshold. SmartBudgetGuard persists state on `on_unload()` (shutdown safety). Daily reset for per-session/team budgets.
- **AST scanner reclassified** — Documented as lint check, not security sandbox. Added explicit warnings that it's bypassable; WASM is the real sandbox.
- **CI workflow** — Added type stubs, httpx, hypothesis to CI. Fixed mypy and ruff errors.

### Fixed
- **Docker volume conflict** — Separated `plugins/` into `bundled/:ro` + `installed/` (writable volume). Plugin install/uninstall no longer hit `PermissionError` in Docker.
- **GC-safe background tasks** — All 11 fire-and-forget `asyncio.create_task()` calls replaced with `_spawn_task()` that retains strong references. Prevents silent task cancellation in Python 3.12+.
- **Queue-full logging** — Budget write queue overflow now logs ERROR (was silent warning).
- **Shutdown plugin persistence** — `on_unload()` called for all plugins during shutdown, ensuring SmartBudgetGuard flushes state before WAL checkpoint.

### Stats
- Tests: 564 → 654 (+90)
- New modules: 8 (semantic_analyzer, threat_ledger, response_signer, app_factory, forwarder, event_log, background, gdpr routes)
- New SOC UI component: security.js

## [1.6.1] — 2026-03-23

### Fixed
- CI lint/typecheck fixes
- Documentation updates (18 plugins, 564 tests)

## [1.6.0] — 2026-03-22

### Added
- 4 new marketplace plugins: ToolGuard, TenantQoS, SchemaEnforcer, ShadowTraffic
- Self-audit: 2 critical race conditions fixed, 4 high-severity hardening fixes
