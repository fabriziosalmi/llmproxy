# LLMPROXY — Piano di Lavoro OGGI

> Audit rigoroso: le sessioni 1-9 del piano precedente sono implementate al 100% come moduli.
> Problema: **3 moduli erano dead code** (scritti ma mai collegati al runtime) + gap di integrazione.
> Questo piano ha chiuso tutti i gap reali rimasti. **COMPLETATO AL 100%.**

---

## SESSIONE A: Wiring — Collegare i moduli orfani al runtime ✅
**Priorita: CRITICA | File: proxy/rotator.py, main.py | Commit: f9addea**

- [x] **A.1** Istanziare `WebhookDispatcher` in `RotatorAgent.__init__` e chiamare `dispatch()` su eventi reali:
  - circuit breaker open → `EventType.CIRCUIT_OPEN`
  - injection blocked (SecurityShield) → `EventType.INJECTION_BLOCKED`
  - auth failure (401) → `EventType.AUTH_FAILURE`
  - endpoint recovered → `EventType.ENDPOINT_RECOVERED`
  - panic activated → `EventType.PANIC_ACTIVATED`
  - budget threshold → `EventType.BUDGET_THRESHOLD`
- [x] **A.2** Istanziare `DatasetExporter` in `RotatorAgent.__init__` e chiamare `record()` dopo ogni request completata in `chat_completions`
- [x] **A.3** Istanziare `TelegramBot` in `RotatorAgent.__init__`, avviare `start_polling()` come background task in `main.py`, collegare `track_error()` al flusso errori
- [x] **A.4** Collegare `notify_ops()` su circuit open e panic

---

## SESSIONE B: Wiring — Metrics & Tracing gap ✅
**Priorita: ALTA | File: proxy/rotator.py, main.py, core/circuit_breaker.py | Commit: f9addea**

- [x] **B.1** Sentry DSN: leggere `config["observability"]["sentry"]["dsn_env"]` in `main.py` e passare a `TraceManager.initialize(sentry_dsn=...)`
- [x] **B.2** `MetricsTracker.track_injection_blocked()`: chiamare da ingress ring block
- [x] **B.3** `MetricsTracker.track_auth_failure(reason)`: chiamare da chat_completions su 401 (4 punti: missing_key, empty_token, jwt_invalid, invalid_key)
- [x] **B.4** `MetricsTracker.set_circuit_state()`: callback `on_state_change` in CircuitBreaker → CircuitManager
- [x] **B.5** `MetricsTracker.set_budget()`: chiamare post-request con budget consumed/limit
- [x] **B.6** `TraceManager.capture_exception()`: chiamare nei 2 catch block critici di proxy_request

---

## SESSIONE C: UI Login Flow — OAuth frontend ✅
**Priorita: ALTA | File: ui/index.html, ui/services/auth.js, ui/main.js, ui/oauth-callback.html, proxy/rotator.py | Commit: c8cd94e**

- [x] **C.1** Login screen: glassmorphism overlay con pulsanti SSO provider + API key fallback
- [x] **C.2** OAuth popup: click → popup a provider OIDC authorize URL con `response_type=id_token`
- [x] **C.3** Callback handler: `oauth-callback.html` estrae id_token dal fragment, invia via postMessage
- [x] **C.4** Token exchange: `POST /api/v1/identity/exchange` → proxy JWT salvato in localStorage
- [x] **C.5** UI state: avatar + nome utente nell'header, pulsante logout
- [x] **C.6** Guard route: se identity enabled e nessun token valido, mostra login overlay
- [x] **C.7** Nuovo endpoint `GET /api/v1/identity/config` per esporre provider al frontend

---

## SESSIONE D: Test Suite ✅
**Priorita: MEDIA | File: tests/ | Commit: 2330f3f**

46 test, 7 file, 100% pass su pytest + pytest-asyncio.

- [x] **D.1** `tests/test_identity.py` (7 test): verify_token disabled/non-JWT/malformed, proxy JWT gen/verify/expire, role mapping
- [x] **D.2** `tests/test_rbac.py` (7 test): admin/user/viewer permissions, multi-role, quota default/exceeded, user roles CRUD
- [x] **D.3** `tests/test_webhooks.py` (6 test): disabled noop, Slack/Teams/Discord/Generic format, severity mapping
- [x] **D.4** `tests/test_chatops.py` (5 test): disabled polling, HITL approve/reject/timeout, error tracking
- [x] **D.5** `tests/test_export.py` (8 test): PII scrub (email/IP/key/bearer), dict redaction, nested scrub, record+file, scrub verify
- [x] **D.6** `tests/test_plugin_engine.py` (8 test): AST scan safe/forbidden (os/subprocess/exec/eval/from-os), allowed modules, syntax error
- [x] **D.7** `tests/test_metrics.py` (5 test): counter increment, error class, injection blocked, budget gauges, circuit state

---

## SESSIONE E: CI/CD & Docker Compose ✅
**Priorita: MEDIA | File: .github/workflows/, docker-compose.yml, .env.example | Commit: 6769d9c**

- [x] **E.1** `.github/workflows/ci.yml`: lint (ruff), test (pytest), syntax check su push/PR
- [x] **E.2** `.github/workflows/docker.yml`: build & push Docker image su tag via GHCR
- [x] **E.3** `docker-compose.yml`: servizio llmproxy + volume + health check + resource limits
- [x] **E.4** `.env.example`: aggiunto OIDC, Sentry, Webhooks, Telegram env vars

---

## STATUS TRACKER

| Sessione | Scope | Stato |
|----------|-------|-------|
| A. Wiring moduli orfani | WebhookDispatcher, DatasetExporter, TelegramBot | ✅ DONE |
| B. Wiring metrics/tracing | Sentry, Prometheus counters, circuit state | ✅ DONE |
| C. UI Login Flow | OAuth popup, callback, session, guard | ✅ DONE |
| D. Test Suite | 7 file, 46 test — 100% pass | ✅ DONE |
| E. CI/CD & Docker Compose | GitHub Actions, compose, .env.example | ✅ DONE |

**PIANO COMPLETATO AL 100%. Zero dead code. Zero gap di integrazione.**
