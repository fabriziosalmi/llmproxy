# LLMPROXY — Piano di Lavoro OGGI

> Audit rigoroso: le sessioni 1-9 del piano precedente sono implementate al 100% come moduli.
> Problema: **3 moduli sono dead code** (scritti ma mai collegati al runtime) + gap di integrazione.
> Questo piano chiude tutti i gap reali rimasti.

---

## SESSIONE A: Wiring — Collegare i moduli orfani al runtime
**Priorita: CRITICA | File: proxy/rotator.py, main.py**

Tre moduli completi non sono mai istanziati ne chiamati. Vanno collegati al ciclo di vita dell'app.

- [ ] **A.1** Istanziare `WebhookDispatcher` in `RotatorAgent.__init__` e chiamare `dispatch()` su eventi reali:
  - circuit breaker open → `EventType.CIRCUIT_OPEN`
  - injection blocked (SecurityShield) → `EventType.INJECTION_BLOCKED`
  - auth failure (401) → `EventType.AUTH_FAILURE`
  - endpoint down (health check fail) → `EventType.ENDPOINT_DOWN`
  - panic activated → `EventType.PANIC_ACTIVATED`
  - budget threshold → `EventType.BUDGET_THRESHOLD`
- [ ] **A.2** Istanziare `DatasetExporter` in `RotatorAgent.__init__` e chiamare `record()` dopo ogni request completata in `chat_completions` (messages, model, latency, tokens, cost)
- [ ] **A.3** Istanziare `TelegramBot` in `RotatorAgent.__init__`, avviare `start_polling()` come background task in `main.py`, collegare `track_error()` al flusso errori
- [ ] **A.4** Cablare `request_approval()` (HITL) nel flusso SecurityShield per soft-violation

---

## SESSIONE B: Wiring — Metrics & Tracing gap
**Priorita: ALTA | File: proxy/rotator.py, main.py, core/tracing.py**

I contatori Prometheus esistono ma non vengono mai incrementati.

- [ ] **B.1** Sentry DSN: leggere `config["observability"]["sentry"]["dsn_env"]` in `main.py` e passare a `TraceManager.initialize(sentry_dsn=...)`
- [ ] **B.2** `MetricsTracker.track_injection_blocked()`: chiamare da SecurityShield quando blocca un injection
- [ ] **B.3** `MetricsTracker.track_auth_failure(reason)`: chiamare da chat_completions su 401 (JWT invalid, API key invalid, expired)
- [ ] **B.4** `MetricsTracker.set_circuit_state()`: chiamare da CircuitManager quando un breaker cambia stato
- [ ] **B.5** `MetricsTracker.set_budget()`: chiamare periodicamente o dopo ogni request con budget consumed/limit
- [ ] **B.6** `TraceManager.capture_exception()`: chiamare nei catch block critici di rotator.py

---

## SESSIONE C: UI Login Flow — OAuth frontend
**Priorita: ALTA | File: ui/index.html, ui/services/api.js, ui/main.js**

Backend SSO e completo (identity/exchange, identity/me) ma la UI non ha nessun flusso OAuth.

- [ ] **C.1** Login screen: modale/overlay con pulsanti "Sign in with Google / Microsoft / Apple" (solo se `identity.enabled = true`)
- [ ] **C.2** OAuth redirect: click → redirect a provider OIDC authorize URL con client_id e redirect_uri
- [ ] **C.3** Callback handler: pagina/route che riceve il code, lo scambia per JWT via provider token endpoint, poi chiama `POST /api/v1/identity/exchange` per ottenere proxy JWT
- [ ] **C.4** Session management: salvare proxy JWT in `localStorage('proxy_key')`, aggiornare header Authorization automaticamente
- [ ] **C.5** UI state: mostrare nome/email utente nell'header, pulsante logout che pulisce il token
- [ ] **C.6** Guard route: se identity enabled e nessun token valido, mostrare login screen invece della dashboard

---

## SESSIONE D: Test Suite
**Priorita: MEDIA | File: tests/**

Coverage attuale quasi zero. Servono test per i moduli critici.

- [ ] **D.1** `tests/test_identity.py`: verify_token con JWT valido/invalido/expired, verify_proxy_jwt, role mapping
- [ ] **D.2** `tests/test_rbac.py`: check_permission per ogni ruolo, get/set_user_roles, check_quota
- [ ] **D.3** `tests/test_webhooks.py`: format_payload per Slack/Teams/Discord, debounce, dispatch mock
- [ ] **D.4** `tests/test_chatops.py`: command parsing, HITL approve/reject/timeout
- [ ] **D.5** `tests/test_export.py`: record + PII scrubbing, daily rotation, file creation
- [ ] **D.6** `tests/test_plugin_engine.py`: AST scan (safe/unsafe code), hot_swap + rollback, install/uninstall
- [ ] **D.7** `tests/test_metrics.py`: track_request incrementa contatori, track_injection_blocked, set_budget

---

## SESSIONE E: CI/CD & Docker Compose
**Priorita: MEDIA | File: .github/workflows/, docker-compose.yml**

Dockerfile esiste ma non c'e pipeline ne compose.

- [ ] **E.1** `.github/workflows/ci.yml`: lint (ruff), type check (mypy), test (pytest), su push/PR
- [ ] **E.2** `.github/workflows/docker.yml`: build & push Docker image su tag/release
- [ ] **E.3** `docker-compose.yml`: servizio llmproxy + volume per SQLite + env_file per secrets
- [ ] **E.4** `.env.example`: template con tutte le env vars documentate (senza valori reali)

---

## STATUS TRACKER

| Sessione | Scope | Stima | Stato |
|----------|-------|-------|-------|
| A. Wiring moduli orfani | WebhookDispatcher, DatasetExporter, TelegramBot | ~40 min | ✅ DONE |
| B. Wiring metrics/tracing | Sentry, Prometheus counters, circuit state | ~25 min | ✅ DONE |
| C. UI Login Flow | OAuth redirect, callback, session, guard | ~35 min | ✅ DONE |
| D. Test Suite | 7 test file, 46 test — 100% pass | ~45 min | ✅ DONE |
| E. CI/CD & Docker Compose | GitHub Actions, compose, .env.example | ~20 min | ✅ DONE |

### Ordine di esecuzione consigliato:
1. **A + B** insieme (wiring) — senza questo i moduli sono dead code
2. **C** (UI login) — completa il flusso SSO end-to-end
3. **D** (test) — valida tutto il wiring
4. **E** (CI/CD) — automatizza
