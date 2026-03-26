# Changelog

All notable changes to LLMProxy are documented here.

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
