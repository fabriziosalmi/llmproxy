# Changelog

All notable changes to LLMProxy are documented here.

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
