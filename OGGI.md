# LLMPROXY — Piano di Lavoro OGGI

> Fase attuale: **Hardening & Debt Cleanup** — eliminazione dead code, wiring completo, test.

---

## SESSIONE F: Dual-Mode Plugin Engine + SDK + Marketplace Plugins ✅
**Priorità: CRITICA | Commit: 34651e3, af53bfa**

- [x] **F.1** `core/plugin_sdk.py`: BasePlugin, PluginResponse, PluginHook, PluginAction enum
- [x] **F.2** `core/plugin_engine.py`: dual-mode (raw functions + BasePlugin class), auto-detection
- [x] **F.3** Strict timeout enforcement: `asyncio.wait_for()` su ogni plugin (class + legacy)
- [x] **F.4** Per-plugin metrics: invocations, errors, blocks, timeouts, avg_latency_ms
- [x] **F.5** `plugins/marketplace/agentic_loop_breaker.py`: SHA-256 prompt hashing, sliding window, block 429
- [x] **F.6** `plugins/marketplace/smart_budget_guard.py`: cost estimation, session+team budget, warn threshold
- [x] **F.7** `plugins/manifest.yaml`: 11 plugin totali (9 legacy + 2 marketplace)
- [x] **F.8** 17 test per marketplace plugins + engine dual-mode

---

## SESSIONE G: 5 Principi FAANG — Compliance ✅
**Priorità: CRITICA | Commit: 0bda9e0**

- [x] **G.1** Principio 1 (Strict Timeouts): `fail_policy` per-plugin (open/closed), default 500ms legacy, 50ms class
- [x] **G.2** Principio 2 (Zero-Blocking): AST scanner blocca `requests`, `urllib`, `sqlite3`, `time.sleep()`
- [x] **G.3** Principio 3 (Typed Contracts): `PluginAction` enum + `__post_init__` validation + engine type-check
- [x] **G.4** Principio 4 (DI State): `PluginState` dataclass con cache/metrics/config/extra slots
- [x] **G.5** 13 test specifici per principi (fail_policy, AST blocking, validation, DI)
- [x] **G.6** README aggiornato con Plugin SDK, marketplace, CI/CD, OAuth, testing

---

## SESSIONE H: WASM/Rust Plugin Pipeline ✅
**Priorità: ALTA | Commit: pending**

- [x] **H.1** Analisi architettura WASM spec — 3 cose buone, 3 bloat scartati (vedi sotto)
- [x] **H.2** `core/wasm_runner.py`: WasmRunner con `asyncio.to_thread` + Extism Plugin
- [x] **H.3** Integrato WasmRunner in `_execute_wasm` con PluginResponse mapping
- [x] **H.4** JSON I/O protocol allineato a PluginResponse (+ compat legacy ALLOW/BLOCK/MODIFIED)
- [x] **H.5** Timeout + metrics + fail_policy per WASM plugins (stesse garanzie)
- [x] **H.6** 15 test mock-based (no Rust toolchain richiesto) — passthrough, block, modify, legacy compat, error handling, engine integration
- [x] **H.7** `plugins/wasm/README.md`: template Rust completo (Cargo.toml, lib.rs, build, manifest)

**Analisi spec Gemini — Verdetto:**
- ✅ asyncio.to_thread (corretto, fix del bug event loop blocking)
- ✅ JSON I/O protocol (standard universale, allineato a PluginResponse)
- ✅ Memory-safe sandboxing (vero valore per marketplace plugins non fidati)
- ❌ WAF PII in Rust (prematura optimization: 0.18ms diff su 8KB, invisibile vs 200ms+ LLM latency)
- ❌ "C10k problem risolto" (FUD: il bottleneck è I/O-bound, non regex CPU)
- ❌ Distribuzione .wasm universale (prematuro con 2 plugin, serve con 50+)

---

## SESSIONE I: Wiring + Dead Code Cleanup ✅
**Priorità: ALTA | Commit: pending**

- [x] **I.1** Dead code cleanup `core/security.py`: rimosso `anomaly_detector` ONNX, `entropy_c` C++, `pii_redactor` PyO3
- [x] **I.2** `mask_pii()` e `_check_pii_leak()` ora funzionano con regex Python (prima erano stub che importavano moduli inesistenti)
- [x] **I.3** Docstring SecurityShield aggiornata (rimosso claim "Native C++/Rust/ONNX")
- [x] **I.4** `PluginState` creato in `RotatorAgent.__init__` con cache, metrics (MetricsTracker), config
- [x] **I.5** `state` passato in ogni `PluginContext` creato in `proxy_request`
- [x] **I.6** `SecurityShield.inspect()` wired nel request chain (pre-INGRESS ring) — multi-turn trajectory detection attivo
- [x] **I.7** Test: shared cache across plugins via PluginState DI (46 test totali pass)

---

## SESSIONE K: UI/UX Surgical Cleanup ✅
**Priorità: MEDIA | Commit: a103905**

- [x] **K.1** Critical HTML nesting fix: `view-proxy` never closed, `plugins-view` nested inside — rebuilt lines 345-489
- [x] **K.2** View ID convention: `plugins-view` → `view-plugins` (allineato a `view-${tabName}` pattern in content.js)
- [x] **K.3** Sidebar labels: "Transparent Proxy" → "Proxy", "Neural Chat" → "Chat", "Operations" → "Settings"
- [x] **K.4** `plugins.js`: raw fetch → `api.fetchPlugins()` / `api.togglePlugin()` (centralizzato in api.js)
- [x] **K.5** `api.js`: aggiunti `fetchPlugins()` e `togglePlugin()` methods
- [x] **K.6** `proxy.js`: wired priority-mode-btn click handler con visual toggle + API call
- [x] **K.7** `chat.js`: rimosso fake guardrail simulation (triggerWord intercept) + fix double appendMessage in compare mode
- [x] **K.8** `settings.js`: aggiunto `FEATURE_DESCRIPTIONS` map con testo specifico per feature
- [x] **K.9** `logs.js`: font priority JetBrains Mono > Fira Code (era invertito)
- [x] **K.10** Topology + terminal sections correttamente contenuti dentro `view-proxy`

---

## SESSIONE L: God Object Split + Budget Persistence + E2E Tests ✅
**Priorità: CRITICA | Commit: pending**

- [x] **L.1 (J.6)** God Object refactor: `rotator.py` da 787→384 righe — 27 route estratte in 6 moduli sotto `proxy/routes/`
  - `proxy/routes/__init__.py`: re-export factory functions
  - `proxy/routes/admin.py`: proxy toggle, status, version, service-info, features, network-info, priority, panic
  - `proxy/routes/registry.py`: endpoint CRUD (toggle, delete, priority, list) + telemetry SSE
  - `proxy/routes/identity.py`: SSO config, /me, token exchange
  - `proxy/routes/plugins.py`: list, toggle, install, uninstall, hot-swap, rollback
  - `proxy/routes/telemetry.py`: health, metrics, logs SSE
  - `proxy/routes/chat.py`: `/v1/chat/completions` con lazy imports (otel, sentry, webhooks)
- [x] **L.2 (J.5)** Budget persistence su SQLite via `app_state` key-value (zero schema changes)
  - Daily budget hydration on startup (`budget:daily_total`, `budget:daily_date`)
  - Fire-and-forget persistence via `asyncio.create_task(store.set_state(...))`
  - `SmartBudgetGuard` 1.1.0: lazy hydration + async persist di session/team spend
  - `PluginState.extra["store"]` per DI del repository nei plugin
- [x] **L.3** `core/tracing.py`: graceful degradation — wrappato opentelemetry in try/except con `_OTEL_AVAILABLE` flag
- [x] **L.4** Bug fix: `EndpointStatus.OFFLINE` non esisteva — corretto in `EndpointStatus.IGNORED`
- [x] **L.5** Lazy imports in route handlers — `MetricsTracker`, `TraceManager`, `EventType` importati dentro le funzioni, non a module-level (evita import chain di otel/sentry in test)
- [x] **L.6** 33 test E2E HTTP-level (`tests/test_e2e.py`)
  - `LightweightAgent`: mock agent che monta le vere route senza importare RotatorAgent
  - `InMemoryRepository`: implementazione BaseRepository in-memory (`tests/conftest.py`)
  - Coverage: health, metrics, version, service-info, network-info, proxy toggle, priority, panic, features, registry CRUD, plugins, identity, chat (auth on/off), budget persistence, state persistence

**Analisi architetturale pre-sessione:**
- Valutato piano 8-sessioni/40-feature → 90% scartato come premature optimization
- Priorità reale: J.6 (God Object) → J.5 (persistence) → E2E tests → poi tutto il resto
- Zero framework aggiunti, zero complessità gratuita

---

## SESSIONE J: Migrazioni Future (Non Bloccanti) — BACKLOG
**Priorità: BASSA**

- [ ] **J.1** Filesystem watcher opzionale per auto-reload in sviluppo (watchdog/inotify)
- [ ] **J.2** Migrazione incrementale default plugins → BasePlugin (uno alla volta, zero urgenza)
- [ ] **J.3** OpenObserve integration per tracing distribuito
- [ ] **J.4** Plugin marketplace UI panel nella dashboard
- [x] **J.5** ~~Budget tracking: persistenza su disco~~ → fatto in L.2
- [x] **J.6** ~~God Object refactor: rotator.py~~ → fatto in L.1
- [ ] **J.7** `sanitize_response()` wiring nel POST_FLIGHT per risposte non-streaming

---

## STATUS TRACKER

| Sessione | Scope | Stato |
|----------|-------|-------|
| F. Dual-Mode Engine + SDK | BasePlugin, PluginResponse, 2 marketplace plugins | ✅ DONE |
| G. 5 Principi FAANG | Timeouts, AST, Contracts, DI, Tests | ✅ DONE |
| H. WASM/Rust Pipeline | WasmRunner, Extism, JSON protocol, 15 test | ✅ DONE |
| I. Wiring + Dead Code | SecurityShield cleanup, PluginState DI, inspect() wired | ✅ DONE |
| K. UI/UX Surgical Cleanup | HTML nesting, view IDs, sidebar labels, API centralization | ✅ DONE |
| L. God Object + Budget + E2E | Route split, SQLite persistence, 33 E2E tests, bug fixes | ✅ DONE |
| J. Backlog | Watcher, migration, OpenObserve, UI, sanitize_response | 🔲 BACKLOG |

**127 test totali — 100% pass. rotator.py dimezzato. Budget persistente. Zero dead code.**
