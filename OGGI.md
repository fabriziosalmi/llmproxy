# LLMPROXY — Piano di Lavoro OGGI

> Fase attuale: **Plugin Engine Quantum Leap** — architettura marketplace-grade.

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

## SESSIONE H: WASM/Rust Plugin Pipeline — TODO
**Priorità: ALTA | File: core/wasm_runner.py, plugins/wasm/**

- [ ] **H.1** Analisi architettura WASM spec (Gemini) — valutare cosa ha senso vs bloat
- [ ] **H.2** `core/wasm_runner.py`: WasmRunner con asyncio.to_thread + Extism Plugin
- [ ] **H.3** Integrare WasmRunner nel plugin engine execute_ring (upgrade `_execute_wasm`)
- [ ] **H.4** JSON I/O protocol: PluginContext → JSON input, WafOutput → PluginResponse mapping
- [ ] **H.5** Timeout + metrics per WASM plugins (stesse garanzie dei Python plugins)
- [ ] **H.6** Test suite WASM (mock-based, non richiede Rust toolchain)
- [ ] **H.7** Documentazione per plugin developer Rust (Cargo.toml, lib.rs template)

---

## SESSIONE I: Wiring PluginState nel Runtime — TODO
**Priorità: MEDIA | File: proxy/rotator.py**

- [ ] **I.1** Creare `PluginState` in `RotatorAgent.__init__` con cache, metrics, config
- [ ] **I.2** Passare `state` in ogni `PluginContext` creato in `chat_completions`/`proxy_request`
- [ ] **I.3** Test di integrazione: plugin accede a `ctx.state.metrics` e `ctx.state.cache`

---

## SESSIONE J: Migrazioni Future (Non Bloccanti) — BACKLOG
**Priorità: BASSA**

- [ ] **J.1** Filesystem watcher opzionale per auto-reload in sviluppo (watchdog/inotify)
- [ ] **J.2** Migrazione incrementale default plugins → BasePlugin (uno alla volta, zero urgenza)
- [ ] **J.3** OpenObserve integration per tracing distribuito
- [ ] **J.4** Plugin marketplace UI panel nella dashboard

---

## STATUS TRACKER

| Sessione | Scope | Stato |
|----------|-------|-------|
| F. Dual-Mode Engine + SDK | BasePlugin, PluginResponse, 2 marketplace plugins | ✅ DONE |
| G. 5 Principi FAANG | Timeouts, AST, Contracts, DI, Tests | ✅ DONE |
| H. WASM/Rust Pipeline | WasmRunner, Extism, JSON protocol | 🔲 TODO |
| I. PluginState Wiring | DI nel runtime RotatorAgent | 🔲 TODO |
| J. Backlog | Watcher, migration, OpenObserve, UI | 🔲 BACKLOG |

**78 test totali — 100% pass. Zero dead code.**
