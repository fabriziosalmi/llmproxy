# Changelog

All notable changes to LLMProxy are documented here.

## [1.21.3] — 2026-04-25

### K.3 — GDPR audit: close PII-scrub gap + log Article 15 export to audit chain

Audit of `proxy/routes/gdpr.py` against `core/security.py` regex set + audit-log integrity surfaced two real issues. Both fixed.

**Issue 1 — Export scrubber missing PII categories**
`core/export.py` `PII_PATTERNS` was a strict subset of `core/security.py` `_REGEX_PII_PATTERNS`. SSN, US/INTL phone, Visa/MC credit card, Amex, and IBAN were absent. If Presidio is not installed AND a log row was written before `mask_pii` ran (e.g. blocked-before-Ring-2 path, or PII embedded in `metadata`), the Article 15 export would leak those categories verbatim. The export scrubber must be strictly **broader** than the runtime masker, not narrower.

Fix: extend `PII_PATTERNS` to cover everything security.py masks, ordered so IBAN runs **before** the 16-digit credit-card pattern (otherwise a German IBAN's 4-digit groups get masked as `<CREDIT_CARD>` and leak the country prefix). IBAN regex is intentionally looser than security.py's — for an export scrubber, false-positives are fine, false-negatives are not.

**Issue 2 — Article 15 access events not in audit chain**
`erase_subject` logs the action to the hash-chained audit log; `export_subject` did not. A leaked admin token could exfiltrate every subject's data with zero trail. Now export writes a parallel `gdpr-export-<subject>` audit row with the records count, mirroring the erase shape so `verify_audit_chain` covers both.

Tests: 7 new `scrub_pii` cases (SSN / US phone / INTL phone / Visa / Amex / IBAN / nested-dict-multi-category) + 1 new GDPR test pinning the export → audit-row contract.

---

## [1.21.2] — 2026-04-25

### K.2 — E2E coverage for shell actions + time-range picker

Five new specs across two files for flows previously running uncovered through CI:

**`11-shell-actions.spec.ts`** (3 tests)
- **Logout**: `#logout-btn` clears `proxy_key` from localStorage and re-shows the login overlay.
- **Panic kill-switch**: cancel must skip `POST /api/v1/panic`; confirm must fire it AND transform the button into HALTED. Pins the confirm-modal-before-destructive-action contract.
- **Action error toast**: a 500 on `DELETE /api/v1/registry/<id>` surfaces a toast in `#toast-container` AND leaves the row in place. Most users hit this before any happy path; the missing coverage was a real gap.

**`12-time-range.spec.ts`** (2 tests)
- Presets toggle `aria-pressed` correctly so a screen reader knows the active range; localStorage persists.
- Switching presets broadcasts: `localStorage.llmproxy.timerange` reflects every change AND only one preset is active at a time.

E2E count: 44 → 49.

---

## [1.21.1] — 2026-04-25

### K.1 — Runtime-tunable cost weight + routing block in spend endpoints

The smart router's `cost_weight` (bias toward cheaper models, 0.0 = ignore cost, 1.0 = full bias) used to be config-only — operators had to edit `config.yaml` and reload to dial cost preference. Three changes land together:

**Live attribute on the orchestrator**
- `ProxyOrchestrator.routing_cost_weight` hydrates from config in `__init__`, then from store in `setup()` so restarts survive runtime changes.
- `smart_router.select_endpoint` reads the agent attribute first, falls back to config. Uses an explicit `is None` check (not truthy) so `cost_weight=0.0` is honored — that's the operator's escape hatch when pricing data is wrong or unavailable.

**Two new admin endpoints**
- `GET /api/v1/routing/config` → `{cost_weight, priority_mode, strategy}` where `strategy ∈ {smart_weighted, priority, performance}`. Surfaces the live routing decision rule, not just the raw config value.
- `POST /api/v1/routing/cost-weight` → accepts `{cost_weight: float in [0,1]}`. Validates range + type, persists to store via `routing:cost_weight` key, logs at `SYSTEM` level.

**`/analytics/spend` + `/analytics/cost-efficiency` now embed routing block**
The two spend endpoints return the same `{cost_weight, priority_mode, strategy}` block alongside the spend numbers, so the dashboard can show "active cost bias: 0.3 (smart_weighted)" next to the dollars — operators see the setting that produced the spend, not just the spend.

8 new tests cover spend response shape, GET routing config across all three strategies, POST happy path, zero-weight round-trip (regression-guards the truthy-check bug), out-of-range rejection, and type rejection.

---

## [1.21.0] — 2026-04-25

### Phase J — telemetry, last legacy services, mobile paper-cuts
Four-part follow-on after Phase I closed the original roadmap. Each piece is independently shippable; bundled here because they all land in the same window.

**J.1 — Wire RUM telemetry to hot interaction points**
- Boot `logger` + `rum` singletons in `main.js` and emit `rum.tabChange` via the store subscriber.
- Add `rum.action()` at the surfaces where adoption matters: guard toggle, endpoint add/toggle/delete/reset-cb, plugin install/toggle/uninstall, webhook test fire, threat mute, palette open + jump + command, panic open + confirm.
- Telemetry stays no-op until a sink is registered, so the change ships dark — no behavior change for users running on the default config.

**J.2 — Migrate the last two legacy services to TypeScript**
- `services/explain.js` → `src/services/explain.ts` (~320 LoC).
- `services/drilldown.js` → `src/services/drilldown.ts` (~720 LoC).
- Internal type shapes added (`EndpointLike`, `AuditRow`, `PluginLike`, `TabBundle`) so the strangler boundary is type-safe even though the four legacy data services (`api`/`store`/`toast`/`timerange`) remain JS by design — they're stable contracts called from both legacy and modern code.
- `main.js` updated to import from the new paths. Closes the `.js → .ts` migration for the services layer.

**J.3 — Backend `/api/v1/logs/client` + frontend `backendSink` wired**
- New POST endpoint in `proxy/routes/telemetry.py`. Accepts `{records: [...], session?: string}` batches; per-batch caps (100 records, 4 KB per message), level normalization, and string-coerced session bound the blast radius if a token leaks.
- Auth inherited from the global `/api/v1/*` middleware. The path can be hard-disabled via `security.client_logs.enabled=false` in `config.yaml` for hardened deploys.
- Records flow through `agent._add_log` so the existing SSE stream + DLQ + operator log view all surface client-origin events alongside server events (prefixed `CLIENT:`).
- `main.js` registers `loggerMod.backendSink` with `minLevel='warn'` (no debug noise on the wire) and the bearer token at flush time. Console sink remains the source of truth — backend is best-effort.
- Tests cover happy path (level normalization, metadata, session), invalid record drop, oversized batch (413), and the disable-toggle (404).

**J.4 — Mobile-first paper-cuts (focused subset)**
- `Drawer`: panel claims 100vw on phones (was 95vw) — drilldown content gets back the cosmetic gutter.
- Identity grid: `grid-cols-1 sm:2 md:4` (was hard-locked at 2 cols, wrapping awkwardly on phones).
- SystemInfo grid: `grid-cols-1 sm:2`.
- Webhooks endpoint row + DataExport file row: stack vertically below `sm`, flex-row above.
- Skipped from the audit: registry table column collapse (separate responsive-table redesign), sidebar/palette modal stacking (legacy `sidebar.js` zone), RBAC sticky-column scroll cue (low-ROI polish).

**J.5 — Light theme: STILL DEFERRED**
- Honest call (again): a proper light theme is a tokens migration (~500-1000 hardcoded color references — `bg-[#0a0a0c]`, `text-white`, `bg-white/[0.03]`). That's a separate Phase K project with its own version bump, not a rider on this batch.

### Numbers
- Pipeline: 249 unit tests passing, route+admin+firewall+openapi backend tests passing (68/68), lint+typecheck+build clean.
- 4 commits land J.1 → J.4; this entry is the J.6 bump.

---

## [1.20.1] — 2026-04-25

### Phase I — DX polish, roadmap closing
Last leg of the original UI-elevation plan. Pure docs + a couple of e2e tests; no runtime change.

**docs/ui/contributing.md** — comprehensive guide for anyone touching `ui/`. Covers the legacy-shell + `src/` TypeScript split, the strangler-fig migration that drove six tab rewrites (Threats → Guards → Endpoints → Models → Plugins → Settings), step-by-step recipes for adding a primitive and migrating a view, the test contract (Vitest unit + Playwright e2e + Storybook-lite stories), and a "where things live" cheat sheet. `CONTRIBUTING.md` now links to it.

**ConfigWarnings e2e** — two new specs in `e2e/10-settings.spec.ts` round out Phase H.4: empty list → green badge + empty surface; non-empty list → row-per-warning amber list with a count badge in the header. E2E tally is now 44 active (was 42).

**CI strict lint** — verified `frontend.yml` already calls `npm run lint` (now strict-by-default since Phase D.3) and `npm run format:check`. No workflow change needed.

### Roadmap closing note

This is the end of the original Phase A → I plan that started at 1.11.1.
- **Phase A** (1.11.2): build pipeline foundation (Vite + TS + ESLint + Vitest + Playwright + multi-stage Docker + CI).
- **Phase C** (1.12.0): Threats vertical slice + 7 primitives + auth + e2e fixtures.
- **Phase D** (1.12.1): hardening — Dependabot bump, CSP cutover, lint:strict, palette e2e.
- **Phase E** (1.13.0): primitive library — Modal, Drawer, Tooltip, Input, Toggle, Table, Tabs + Storybook-lite.
- **Phase F** (1.14.0): Guards vertical slice.
- **Phase G** (1.15.0–1.19.0): Endpoints, Models, Plugins, Settings, Threats sub-sections.
- **Phase H** (1.20.0): UX polish — mobile reflow, drilldown filter, config warnings UI, telemetry layer.
- **Phase I** (1.20.1): DX polish — UI contributing guide, e2e coverage closure.

Net delta from 1.11.1 → 1.20.1:
- 0 → **15 primitives** + the `cx()` composer in `src/ui/`.
- 0 → **6 tabs** migrated end-to-end to TypeScript.
- 0 → **249 unit tests** (Vitest, happy-dom).
- 5 → **44 e2e tests** (Playwright).
- 13 lint warnings → **0**, with `--max-warnings=0` enforced in CI and pre-commit.
- 0 → **8 Dependabot alerts cleared** (all dev-only) + CSP `unsafe-eval` removed + Tailwind JIT CDN dropped.
- 1 → **3 legacy services retired** (`dialog.js`, `drawer.js`, `vendor/tailwind.js`).
- ~70% of the legacy `components/*.js` LoC migrated; the rest is the source-tree fallback shell that stays by design.

---

## [1.20.0] — 2026-04-25

### UX polish — Phase H
Five-piece polish pass after Phase G closed the operator-console migration. Each item is isolated, but together they remove real paper-cuts the audit flagged.

**H.1 — Mobile reflow**
- `Modal`: `max-h-[85vh] overflow-y-auto` so long body content stays scrollable inside the dialog and the action buttons never slip below the fold on small viewports.
- `Tabs`: tablist now scrolls horizontally (`overflow-x-auto` + `shrink-0` per tab) instead of clipping when labels exceed the row.

**H.2 — Theme toggle: DEFERRED**
- Honest call: a proper light theme is 2-3 days of class migration (the design uses literal `text-white`, `bg-white/[0.03]`, … not token vars). Operator console — dark is the standard. Captured here so the deferral is explicit, not silent.

**H.3 — Drilldown context loss**
- `services/drilldown.js` was issuing `/api/v1/audit?limit=N` without the active time-range filter. Three call sites (endpoint / request / model drilldown) now append `&from=<iso>` from `timerange.sinceEpochMs()`. Opening an inspector from a 4h-windowed view no longer silently widens to "all time".

**H.4 — Config warnings UI**
- `core/startup_checks.py` captures warnings at module scope and exposes `get_startup_warnings()`; `run_startup_checks` stashes both the warnings list and any `StartupError` message.
- `proxy/routes/admin.py`: new `GET /api/v1/config/warnings` (admin-auth).
- `ui/src/views/settings/ConfigWarnings.ts`: Settings widget mounted above every other section so config drift is loud. Green badge when none, amber list with ⚠ icon per warning otherwise, ErrorState with retry on backend failure.

**H.5 + H.6 — Frontend telemetry**
- `ui/src/services/logger.ts`: pluggable sinks (`consoleSink` + `backendSink` with batched POST + `navigator.sendBeacon` on `pagehide`). `createLogger` / `installGlobalErrorHandlers` wire `window.onerror` + `unhandledrejection` into the same funnel. A throwing sink never silences the others.
- `ui/src/services/rum.ts`: vendor-agnostic facade — `pageView` / `tabChange` / `action` / `error`. Default sink is no-op so the bundle stays vendor-free; operators plug in PostHog / Datadog / self-hosted at boot via `rum.setSink({ track })`. `pageView` records `previous_view_ms` dwell on consecutive views; `tabChange` threads `from` through the sequence.
- Both modules ship typed tests (19 new). Wiring into `main.js` / view code is a separate phase — the modules are ready to plug in, intentionally not yet imported anywhere so we don't change runtime behavior in this minor.

**Tests + numbers**
- Unit tests: 249 / 249 (was 230). 19 new across logger + rum.
- E2E: 42 active (unchanged — Phase H is internal polish, no new user-visible flow).
- 0 lint warnings, typecheck clean, build OK.

---

## [1.19.0] — 2026-04-25

### Operator console — Threats strangler-fig closed (Phase G.5)
Last leg of Phase G. Every Threats sub-section that was left in `components/threats.js` after Phase C.2 has been migrated to a TS module under `src/views/threats/sections/`. Threats is now the first tab fully migrated end-to-end: KPI grid (C.2) + event feed (C.2) + sections (G.5).

**Sections — `ui/src/views/threats/sections/`**
- `BudgetGauge.ts` — pure `computeBudget()` + renderer that flips emerald → amber → rose with usage. Tracking-only mode when `limit=0`; "no budget" hint when nothing is configured.
- `FirewallStats.ts` — scanned + blocked counters with rose/emerald tone on blocked, optional signature breakdown table.
- `EndpointBreakdown.ts` — pure `parseEndpointBreakdown()` + renderer that tiers err% with rose / amber / emerald. Aggregates per-endpoint counters from a Prometheus text exposition.
- `RingLatency.ts` — bars per ring (ingress / pre_flight / routing / post_flight / background) with P50/P95/P99/count, and a separate `renderTtft()` for the streaming card. TTFT P95 color flips above 500ms / 1000ms.
- `RingTimeline.ts` — last-N traces with width-proportional segments per ring + a separate upstream segment, TTFT badge when streaming.
- `ThreatChart.ts` — wraps the Chart.js bar-chart over 24 hours, subscribes to `window 'llmproxy:threat-event'` dispatched by the EventFeed.

**Orchestrator — `ui/src/views/threats/index.ts`**
- New `mountThreatsSections({ budget, firewall, breakdown, ringLatency, ttft, ringTimeline, chartCanvas }, { api, poll, onFirewallState })`.
- Polls `/metrics`, `/api/v1/guards/status`, `/api/v1/metrics/latency`, `/api/v1/metrics/ring-timeline` in parallel — each section degrades independently when its source 503s.
- `onFirewallState` bridges live firewall status into the shared store so the Guards view reflects whether the WAF is running.

**Strangler fig**
- `components/threats.js` bails `refreshMetrics`, `refreshLatencyData`, and `initChart` when `_tsMounted` is true. Legacy renderers stay only as the source-tree fallback.

**Tests**
- 25 new unit tests across the five rendered sections + the two pure helpers (`computeBudget`, `parseEndpointBreakdown`). ThreatChart skipped — depends on the Chart.js global; exercised through the existing Threats e2e.
- Total tally: 230 / 230 unit tests (was 205), 42 active e2e tests (unchanged — the existing 04-threats-drilldown spec covers the integration).

**Phase G complete.** Six tabs migrated end-to-end (Threats / Guards / Endpoints / Models / Plugins / Settings); the only legacy `.js` modules left in `components/` are the four shells that own the dynamic-import handoff.

---

## [1.18.0] — 2026-04-25

### Operator console — Settings vertical slice (Phase G.4)
Sixth tab migrated to the C.2 pattern. `components/settings.js` (230 LoC) now delegates each of its five sections — Identity, RBAC, Webhooks, Data Export, System Info — to its own TS module under `src/views/settings/`. Each section refreshes independently: a 503 on `/rbac/roles` doesn't blank out `/webhooks`.

**Sections — `ui/src/views/settings/`**
- `SystemInfo.ts` — Card wrapping Version + Endpoint. Skeletons on first paint, "unavailable" fallback when /version or /service-info errors.
- `Identity.ts` — Auth mode + SSO status + the authenticated-user grid (Provider / Email / Roles / Permissions). Empty state when `/identity/me` reports unauthenticated; per-call fallback when either endpoint errors.
- `RbacMatrix.ts` — Permission × role table with a check / dash cell per intersection, footer summary with role and permission counts. ErrorState with retry on `/rbac/roles` 503.
- `Webhooks.ts` — Endpoint list with target Badge per row (slack / teams / discord / generic), available-events chip set, **Test Fire** button that hits `/webhooks/test` with separate success / error toast paths. Empty state when webhooks are disabled in `config.yaml`.
- `DataExport.ts` — Output dir + PII Scrub / Compress option Badges + recent-files list. Empty state when export is disabled; "no files yet" when enabled but list is empty.

**Orchestrator — `ui/src/views/settings/index.ts`**
- `mountSettingsView({ identity, rbac, webhooks, export, system }, { api, toast })` returns a `refreshAll()` that fans out to each section's refresh via `Promise.allSettled` so a slow / failing endpoint never blocks the others.

**Strangler fig**
- `components/settings.js` keeps the legacy renderers behind a `_tsMounted` flag; `refreshAll()` delegates to the TS refresh once mounted. The `nav-settings` click listener still drives a refresh for the source-tree fallback.
- `index.html` wraps each section in a `*-host` div so source-tree fallback still renders something.
- `services/api.js` gains `api.fetchIdentityConfig()` so the orchestrator doesn't issue raw fetch calls.

**Tests**
- 15 new unit tests across Identity / RbacMatrix / Webhooks / DataExport. Covers the disabled / empty / error / happy paths per section, the Test Fire success and failure toasts, and the RBAC matrix rendering.
- One new e2e spec (`e2e/10-settings.spec.ts`) — 5 tests covering each section end-to-end with stubbed backend responses.
- Total tally: 205 / 205 unit tests (was 190), 42 active e2e tests (was 37).

---

## [1.17.0] — 2026-04-25

### Operator console — Plugins vertical slice (Phase G.3)
Fifth tab migrated to the C.2 pattern. `components/plugins.js` (312 LoC) now delegates the toolbar (Rollback / + Install / Reload), the install form, and the plugin grid to TypeScript views in `src/views/plugins/`.

**Catalog + types — `ui/src/views/plugins/types.ts`**
- `Plugin` shape (name, hook, entrypoint, timeout_ms, fail_policy, version, ui_schema), `PluginStats` (invocations, errors, blocks, avg_latency_ms, latency_percentiles), `RING_OPTIONS` for the install dropdown, `RING_INTENT` mapping each ring to its closest Badge intent.

**Plugin card — `ui/src/views/plugins/PluginCard.ts`**
- Card primitive wrapping a header (ring badge, timeout, fail-policy, version, enabled dot), description, four-stat row (calls / blocks / err% / avg ms with tone tied to value), optional latency P50/P95/P99, optional read-only `ui_schema` block, and three actions:
  - **Inspect** forwards `data-drilldown="plugin:<name>"` to the existing service.
  - **Toggle** flips its label with the enabled state; calls `togglePlugin`, refreshes, toasts the result.
  - **Uninstall** opens a Modal confirm with `danger: true` before calling DELETE.

**Install form — `ui/src/views/plugins/InstallForm.ts`**
- Input primitives + two styled native `<select>`s (hook + fail policy). Same validation as legacy: name must match `^[a-z_][a-z0-9_]*$/i` (Python identifier convention), entrypoint must contain `:` (module:Class).
- Busy state during submit. Two distinct toast paths: backend `status: "failed"` surfaces `result.detail`; thrown error surfaces the exception message.

**Orchestrator — `ui/src/views/plugins/index.ts`**
- `mountPluginsView({ grid, formHost, rollbackBtn, installToggle, reloadBtn }, { api, toast, poll })`.
- Plugins + stats fetched in parallel — cards render even when `/stats` 503s.
- Re-clones the three toolbar buttons to strip legacy listeners. Skeleton on first load, ErrorState on registry fetch failure.

**Strangler fig**
- `components/plugins.js` keeps the legacy renderer behind a `_tsMounted` flag.
- `index.html` wraps the legacy install form in `#plugin-install-form-host` so the markup is functional even without a Vite build.
- `services/api.js` gains `reloadPlugins()` (POST `/api/v1/plugins/hot-swap`) so the orchestrator doesn't issue raw fetch calls.

**Tests**
- 18 new unit tests across PluginCard + InstallForm.
- One new e2e spec (`e2e/09-plugins.spec.ts`) — 6 tests covering card rendering, Inspect drilldown, Toggle POST shape, Uninstall confirm cancel, Install happy path, Reload hot-swap.
- Total tally: 190 / 190 unit tests (was 172), 37 active e2e tests (was 31).

---

## [1.16.0] — 2026-04-25

### Operator console — Models vertical slice (Phase G.2)
Fourth tab migrated to the C.2 pattern. `components/models.js` (127 LoC) now delegates the KPI grid + the chat/embedding tables to TypeScript views. Smaller scope than Endpoints — Models is read-only — but it consolidates the search-and-filter pattern the upcoming Plugins / Logs migrations will need.

**Catalog + types — `ui/src/views/models/types.ts`**
- `Model` shape (id, owned_by, …), `EMBEDDING_PREFIXES` shared with the legacy detector, plus the bespoke provider-color palette (eleven providers, finer-grained than the Badge primitive's six intents).

**KPI tiles — `ui/src/views/models/Kpi.ts`**
- Three MetricTiles (Active Models / Providers / Embedding Models) with provenance text per tile naming the source field on `/v1/models` and the embedding-prefix list. Loading skeleton on first paint, em-dash + tooltip on backend error.

**Models table — `ui/src/views/models/ModelsTable.ts`**
- Wraps the Table primitive with two cell renderers: id with an inline `EMB` badge for embeddings, and provider name in its bespoke color. Per-row Inspect button forwards `data-drilldown="model:<id>"` to the existing drilldown service.

**Orchestrator — `ui/src/views/models/index.ts`**
- `mountModelsView({ kpis, search, table }, { api, poll, initial })`. Splits visible models into chat + embedding, renders two tables (the embedding table only mounts when `embedding.length > 0`), and pins a footer line with the visible count.
- Search input is debounced 150ms (matches the legacy 1:1). Empty + filter-empty + error states each have their own surface.

**Strangler fig**
- `components/models.js` keeps the legacy renderer as the source-tree fallback. `_tsMounted` flag bails its renderModelsTable / refreshModels once the TS chunk lands.
- `index.html` wraps the legacy KPI grid in `#models-kpi-grid-host` so the markup is functional even without a Vite build.

**Tests**
- 14 new unit tests across types / Kpi / ModelsTable.
- One new e2e spec (`e2e/08-models.spec.ts`) — five tests covering KPI population, EMB badge presence, drilldown attribute, debounced search round-trip, no-match empty state.
- Total tally: 172 / 172 unit tests (was 157), 31 active e2e tests (was 26).

---

## [1.15.0] — 2026-04-25

### Operator console — Endpoints vertical slice (Phase G.1)
The heaviest legacy view (`components/registry.js`, 314 LoC) now renders through TypeScript primitives. Third tab migrated to the C.2 pattern after Threats and Guards — biggest validation of the primitive set.

**Add-endpoint form — `ui/src/views/endpoints/AddForm.ts`**
- Composed of Input primitives plus a styled native `<select>` for the provider dropdown (a Select primitive will land later).
- Exposes a `{ root, open, close, focus, isOpen }` handle so the orchestrator and the onboarding empty-state can drive it without DOM hunting.
- Same client-side validation as the legacy version: id pattern (`^[a-z0-9][a-z0-9_-]*$`), URL must parse and use http/https. Errors surface inline; Cancel resets the editable fields.
- Submit shows a busy state and reverts on backend failure, surfacing a toast with the upstream message.

**Registry table — `ui/src/views/endpoints/RegistryTable.ts`**
- Wraps the Table primitive with endpoint-specific cell renderers: Badge for status (Live/Degraded/Ignored), Badge with optional indicator dot for the circuit state (Closed/Half/Open), inline failure-count ratio when failures > 0, priority +/- controls that floor at 0.
- Per-row actions through the Button primitive: **Inspect** forwards `data-drilldown="endpoint:<id>"` to the existing drilldown service; **Reset CB / Toggle** call the backend and refresh; **Delete** opens a Modal confirm first (`danger: true`) and only fires the DELETE on confirmation.

**Onboarding empty state — `ui/src/views/endpoints/EmptyState.ts`**
- Composes the EmptyState primitive plus a collapsed `<details>` block with the env-var path for users who'd rather configure providers via `.env` (LM Studio / vLLM / Ollama).

**Orchestrator — `ui/src/views/endpoints/index.ts`**
- `mountEndpointsView({ view, addToggle, registry, formHost }, { api, toast, initial, poll })`.
- Polls `/api/v1/registry` (default 10s, configurable) and re-renders the body in place. ErrorState with retry surfaces when the fetch fails.

**Strangler fig — `components/registry.js`**
- Legacy `fetchRegistry` / `renderRegistry` still run during the dynamic-import roundtrip so the page is functional from the first paint, then bail (`_tsMounted` flag) once TS has mounted.
- Source-tree fallback (no Vite build) keeps the original markup live — the `#add-endpoint-form-host` wrapper preserves the original `#add-endpoint-form` inside it as a fallback child.

**Tests**
- 16 new unit tests across AddForm + RegistryTable.
- One new e2e spec (`e2e/07-endpoints.spec.ts`) — six tests covering the registry table end-to-end (status/circuit badges, drilldown wiring, Reset CB, Delete cancel + confirm, Priority up).
- Existing `e2e/03-add-endpoint.spec.ts` switched to data-testid selectors so the same spec passes against both the legacy markup and the TS-mounted form.
- Total tally: 157 / 157 unit tests (was 141), 26 active e2e tests (was 20).

---

## [1.14.0] — 2026-04-25

### Operator console — Guards vertical slice (Phase F)
Second view migrated to the C.2 pattern, validating the primitives shipped in 1.13.0 against a new shape. Guards now renders the master toggle, priority steering, and the eight-card guards grid through the TypeScript primitives — provenance ℹ tooltips on every guard, optimistic-revert toggles, and proper empty / loading / error states.

**Catalog with provenance — `ui/src/views/guards/catalog.ts`**
- Eight guards (injection_guard, language_guard, link_sanitizer, pii_masker, firewall, rate_limiter, zero_trust, circuit_breaker), each carrying a `provenance` line that names the trigger, the threat it counters, and where to flip it when read-only (config key, env var, or "AUTO" for automatic systems).
- Provenance text surfaces in a Tooltip-primitive popover anchored on each card's ℹ button — replaces the bare `title=""` approach for a focus-accessible explanation.

**Components — `ui/src/views/guards/`**
- `GuardCard.ts` — one card per spec. Toggleable specs render a Toggle primitive in the header; static specs render a Badge with the catalog `staticStatus` (or a status override for the firewall surface).
- `Grid.ts` — wraps the eight cards in the responsive grid. Built-in skeleton during initial load, ErrorState with retry on backend failure, optimistic re-render on toggle that reverts on backend rejection.
- `Toggles.ts` — `mountToggleCard(host, opts)` mounts a single big-toggle card (master proxy, priority steering). Disables the switch while the request is in flight, applies the canonical response on success, and surfaces success / failure toasts.
- `index.ts` — `mountGuardsView({ master, priority, grid }, { api, toast, initial, poll })`. Single entry point, returns a stop function that kills the polling loop.

**Strangler fig**
- `components/guards.js` keeps the Cache Performance card and the Operations buttons (reset firewall, clear caches, reset security, reload config). Those migrate when each becomes painful.
- Legacy `renderGuards()` / `initProxyToggle()` / `initPriorityToggle()` still run during page boot so the markup is functional during the dynamic-import roundtrip (~50ms). The TS module then takes over via `replaceChildren`. A `_tsMounted` flag guards the legacy `renderGuards()` from overwriting TS state on subsequent store updates.
- `index.html` keeps the legacy markup inside two new mount hosts (`#guards-master-host`, `#guards-priority-host`) — when no Vite build is present, the page still renders something sensible.

**Tests**
- 16 new unit tests across GuardCard / Grid / Toggles. Covers toggleable vs static specs, the firewall override path, optimistic revert on toggle failure, the in-flight disable state, and the empty / loading / error transitions.
- One new e2e spec (`e2e/06-guards.spec.ts`) — five tests around card rendering, toggling a guard, master toggle keyboard activation, and the firewall "OFF · <reason>" path.
- Total tally: 141 / 141 unit tests (was 125), 20 active e2e tests (was 16).

**Knock-on cleanup**
- `Toggle` primitive's `testId` now lands on the `[role="switch"]` button itself (not the wrapper div), so callers that pluck out the switch keep the test selector. Existing Toggle tests unaffected.

---

## [1.13.0] — 2026-04-25

### Component library expansion — Phase E
Builds out the primitive set so the upcoming view migrations (Phase F: Guards, Endpoints, Models, Plugins, Settings) compose against a stable surface instead of repeatedly inventing buttons, dialogs, and tables. No user-visible feature is added — this is the runway for Phase F.

**Storybook-lite gallery — `ui/dev/primitives.html` (E.1)**
Curated visual + interactive gallery, grouped by primitive. Lives in dev only; not registered in `vite.config.rollupOptions.input`, so it never ships in `dist/`. Open with `make dev-ui`, browse `http://localhost:5173/ui/dev/primitives.html`. Stories are typed (`Story` interface) and registered in `ui/src/dev/stories.ts` — adding a story when shipping a new primitive variant is a one-line change.

**Modal primitive — `ui/src/ui/Modal.ts` (E.2)**
Replaces `services/dialog.js`. Three entry points: `createModal()` for general-purpose dialogs, `confirm()` for boolean prompts, `prompt()` for single-line input. role=dialog + aria-modal=true, focus trap with restoration, Escape / backdrop-click / button cancellation, danger=true accent for destructive actions. The `prompt()` path supports synchronous validation that keeps the modal open until the input clears.
- Six call-sites refactored: registry / plugins (×2) / security / drilldown (×2) / main (panic btn) / chat (key prompt). Each now uses dynamic import (`const { confirm } = await import('../src/ui')`) — same pattern threats.js already uses to delegate to TS primitives without breaking the source-tree fallback at the entry-point level.
- `ui/services/dialog.js` deleted (was 226 lines).

**Drawer primitive — `ui/src/ui/Drawer.ts` (E.3)**
Replaces `services/drawer.js`. Long-lived non-modal investigation surface used by the explain pane and the drilldown inspector. Returns a `DrawerHandle` with `setTitle`, `setBody`, `close`, and an `isOpen` flag. Single-drawer model preserved — opening a second drawer reuses the existing panel rather than stacking.
- `services/explain.js` and `services/drilldown.js` adopted dynamic import. `ui/services/drawer.js` deleted (was 158 lines).

**Tooltip primitive — `ui/src/ui/Tooltip.ts` (E.4)**
Popover-style hint anchored to a trigger. `attachTooltip(target, opts)` returns a `destroy()` cleanup. 200ms hover delay, immediate on keyboard focus (a11y win over bare `title=""`). Single shared host so triggers don't pollute their stacking context. Auto-flips top↔bottom when the viewport would clip.

**Input / FormField primitive — `ui/src/ui/Input.ts` (E.5)**
Labeled form field with help text and inline error. `createInput(opts)` returns the wrapper plus `{ input, setError, setValue, getValue }`. Auto-clears the error on next keystroke (correct-as-you-type UX). aria-describedby flips between help and error ids based on visible state; aria-invalid set on error.

**Toggle / Switch primitive — `ui/src/ui/Toggle.ts` (E.6)**
Accessible on/off switch using `role="switch"` so screen readers announce "switch on/off" rather than "checkbox". Click + Space + Enter all flip; disabled blocks all three. `setChecked(next, fire?)` lets callers update state programmatically without forcing the listener.

**Table primitive — `ui/src/ui/Table.ts` (E.7)**
Generic, sortable, with built-in empty state. Each column declares key/label/align/width/sortable/render/sortValue. `setRows()` re-renders the body in place so external scrolling state and the sticky header survive. Sortable headers click-toggle asc/desc with `aria-sort` mirroring the visible indicator.

**Tabs primitive — `ui/src/ui/Tabs.ts` (E.8)**
Multi-pane navigation with arrow-key support (Left / Right / Home / End), roving tabindex, full ARIA cross-links. Pane render functions are called lazily on first activation and cached, so heavy panes don't pay their cost up front. Optional inline Badge per tab for unread counts.

**Numbers**
- Primitives shipped in `ui/src/ui/`: 13 (Button, Card, Badge, EmptyState, ErrorState, Skeleton, MetricTile, Modal, Drawer, Tooltip, Input, Toggle, Table, Tabs) plus the `cx` class composer.
- Unit tests: 125 / 125 (was 68 at 1.12.0).
- Build: 6 dynamic-imported chunks now (Modal, Drawer, Tooltip, Input, Toggle, Table, Tabs); main bundle is 138 KB (37 KB gzip), down ~3 KB from 1.12.0 due to dialog/drawer code-split out.
- Three legacy `.js` services deleted (dialog 226 LoC, drawer 158 LoC).

---

## [1.12.1] — 2026-04-25

### Hardening — Phase D
Tier-1 cleanup of debt and security paper-cuts surfaced after 1.12.0. No new product surface; the entire diff is "finishing what we started".

**Dev-dependency bumps (D.1)**
- `vite` 5 → 7, `vitest` 2 → 3, `@vitest/coverage-v8` 2 → 3, `happy-dom` 15 → 20, `postcss` → 8.5.10. Closes 8 GitHub Dependabot alerts (1 critical happy-dom RCE, 2 high happy-dom, 5 moderate vite/esbuild/postcss). All vulnerabilities were dev-only — production never shipped these — but the alerts created noise in the security tab. `npm audit` now reports 0 vulnerabilities. 68 / 68 unit tests still pass; build is unchanged.

**CSP cutover + Tailwind CDN removal (D.2)**
- The CSP for `/ui/*` no longer allows `script-src 'unsafe-eval'`. The Tailwind JIT CDN script (`ui/public/vendor/tailwind.js`, ~400 KB) is removed. Tailwind is now compiled exclusively at build time via PostCSS — the build was already wired in 1.11.2, this commit retires the runtime fallback.
- `chat.html` previously relied on the JIT CDN for styling; it now imports the same compiled `style.css` so both pages share a single CSS bundle (`dist/assets/dialog-*.css`).
- Behavior change: `python main.py` against the source tree (no `npm run build`) still serves the proxy and the admin UI markup — but Tailwind utility classes will no longer be styled. `install.sh`, `Makefile build-ui`, and the multi-stage Dockerfile all build the bundle automatically; the only path that hits the unstyled state is "I cloned and ran main.py without Node available", which is rare and now logs a clear warning naming the fix.

**Legacy lint cleanup (D.3)**
- 13 lingering ESLint warnings across `chat.js`, `components/{guards,logs,plugins,security}.js`, `services/drilldown.js` and `main.js` cleared: unused imports removed, optional `catch` bindings dropped, dead vars deleted, `let` → `const` where reassignment never happened.
- `npm run lint` is now strict-by-default (`--max-warnings=0`). The transitional `lint:strict` script is gone. Lefthook pre-commit blocks new warnings rather than just new errors. CI inherits the new floor without any workflow change.

**End-to-end coverage for the command palette (D.4)**
- `e2e/05-command-palette.spec.ts` lifted out of stub status. Five specs cover Cmd+K open / Escape close, Ctrl+K cross-platform alias, type-to-filter + Enter-to-navigate, the `>` jump-to mode kind hints, and the empty-state copy when no command matches.
- E2E tally: 16 active (was 11 + 1 stub).

---

## [1.12.0] — 2026-04-25

### UI elevation — Phase C, vertical slice on Threats
Builds on the 1.11.2 foundation. Lifts Threats from "metrics dashboard" toward "operator console" by introducing reusable primitives, rewriting the heaviest sections in TypeScript, and wiring real end-to-end coverage for the two journeys that were stubbed last sprint.

**Design tokens + UI primitives (`ui/src/ui/`)**
- Semantic tokens layered on top of `style.css`: spacing scale, radius, elevation, intent palette (rose/emerald/amber/red/blue/slate), motion durations + easings, z-scale.
- 6 primitives — each a factory function returning an `HTMLElement`, no virtual DOM, no framework: `Button` (4 variants × 3 sizes, focus-visible ring, leading icon, aria-pressed), `Card` (flat/raised, optional interactive role=button + Enter/Space), `Badge` (6 intents, optional indicator dot), `EmptyState` (role=status + primary/secondary CTAs), `ErrorState` (role=alert, collapsible detail block, retry CTA), `Skeleton` (line/block/circle, repeat for lists).
- 7th primitive `MetricTile` lives alongside the others — surfaces a "Why this metric?" ℹ button per tile with a native title-attribute tooltip naming the source counter and time window.
- `cx()` class composer (string | array | dict, dedup) keeps the composition pure and unit-testable.
- 36 unit tests across the 7 primitives + composer, all in happy-dom.

**Threats view rewrite (`ui/src/views/threats/`)**
- KPI grid now renders 8 `MetricTile`s with provenance tooltips (`Sum of llm_proxy_requests_total since boot`, `1 - (blocked / requests)`, etc.). Loading skeletons show on first paint; em-dash + tooltip surface on backend errors.
- Live event feed (`ThreatEventFeed` class) replaces the legacy SSE renderer. Each event row carries three actions:
  - **Investigate** — wires `data-drilldown="request:<id>"` to the existing drilldown service. Shown only when the event has a request id.
  - **Explain** — wires `data-explain="rule:<signature>"` to the existing explain pane. Shown only when the event names a signature.
  - **Mute** — toggles a category-level mute (level + signature, or level + truncated message). Persists to `localStorage["llmproxy:muted-threats"]`. Aria-pressed reflects state. Muted events are filtered locally and a footer "X muted hidden" line summarizes.
- Connection state is now visible: a `Badge` in the feed header shows `idle / connecting / live / disconnected / reconnecting / awaiting auth`. Repeated SSE errors flip to an `ErrorState` with a Reconnect button.
- Empty state appears the moment the feed has no events to show — with copy that reflects whether the silence is genuine or a side effect of mutes.
- Strangler fig: budget gauge, firewall stats, per-endpoint breakdown, ring latency, ring timeline, threat chart and security pipeline still render through `components/threats.js`. They migrate incrementally; the chart receives event updates through a new `window 'llmproxy:threat-event'` `CustomEvent` dispatched by the new feed.
- Source-tree fallback (no `npm run build`) keeps working: `components/threats.js` uses a `try/catch`'d dynamic import; missing chunks fall back to the legacy SSE handler and the original DOM markup is preserved as a fallback inside the new mount points.

**End-to-end coverage (`ui/e2e/`)**
- New `fixtures/auth.ts` — Playwright extension that pre-seeds `localStorage.proxy_key` so authed tests skip the login overlay. Reads `LLMPROXY_E2E_KEY` from env, falls back to the CI key.
- New `fixtures/sseMock.ts` — replaces `window.EventSource` with a fake driven from tests via `window.__sseEmit(data)` / `window.__sseError()`. Lets the threat-feed flow run deterministically without a real backend SSE source.
- `03-add-endpoint.spec.ts` — three new specs covering empty-id rejection, non-http URL rejection, and the success path (registry POST stubbed via `page.route`, asserts the new id appears in `#registry-container`).
- `04-threats-drilldown.spec.ts` — three new specs covering the KPI provenance ℹ button, the actions wired to a streamed event (`data-drilldown` / `data-explain` / mute aria-pressed), and the mute → empty-state → localStorage round trip.
- E2E tally: 11 active + 1 stub (palette spec deferred behind the keybinding contract), up from 5 active + 3 stub in 1.11.2.

**Numbers — local pipeline**
- Unit tests: 68 / 68 (was 7).
- Lint: 0 errors, 13 legacy warnings (down from 16; logs.js regex escapes were fixed in 1.11.2).
- Build: 44 modules transformed, `dist/assets/main-*.js` 142 KB / 38 KB gzip; new dynamic-imported `dist/assets/index-*.js` 22 KB / 7 KB gzip.

---

## [1.11.2] — 2026-04-25

### Frontend foundation — Phase A
Lays the groundwork that previous UI sprints were missing: a real build pipeline, type checking, lint/format, unit + e2e tests, and CI. Backend is unchanged. Net result: the frontend can now be refactored aggressively without flying blind.

**Build pipeline (`ui/vite.config.ts`, `ui/tsconfig.json`, `ui/tailwind.config.js`)**
- Vite 5 bundler with `index.html`, `chat.html`, `oauth-callback.html` as entry points. Output to `ui/dist/` (~140 KB main JS gzipped to 38 KB, ~40 KB CSS gzipped to 9 KB, source maps included).
- Tailwind 3 via PostCSS at build time. The `vendor/tailwind.js` JIT CDN is kept as a fallback so `python main.py` without `npm run build` still produces a usable UI; CSP `unsafe-eval` removal is queued behind the cutover.
- TypeScript medium-strict (`strict: true`, `noImplicitAny: false`, `allowJs: true`). Existing `.js` files keep working; new code is fully typed; gradual migration via JSDoc + per-file conversion.
- Vendored assets relocated from `ui/vendor/` to `ui/public/vendor/` so Vite passes them through unchanged. URLs (`/ui/vendor/...`) unaffected.

**Lint + format + pre-commit (`ui/eslint.config.js`, `ui/.prettierrc.json`, `lefthook.yml`)**
- ESLint 9 flat config, TypeScript-aware, browser/node globals. Pragmatic rules — errors block, warnings warn (existing codebase: 0 errors, 13 warnings to clean up gradually).
- Prettier 3 with project conventions (4-space indent, single quotes, 120 cols).
- Lefthook pre-commit: `ruff` + `py-syntax` for Python, `eslint --fix` + `prettier --write` + `tsc --noEmit` for the UI, all scoped to staged files. Activate with `brew install lefthook && lefthook install`.

**Unit tests (`ui/vitest.config.ts`, `ui/__tests__/`)**
- Vitest 2 with happy-dom + v8 coverage.
- First suite: `__tests__/store.test.ts` exercises pub/sub, polling visibility-pause, requiredTab gating, and stop() — 7 tests, ~500 ms.

**End-to-end tests (`ui/playwright.config.ts`, `ui/e2e/`)**
- Playwright with auto-started backend in dev (override via `LLMPROXY_SKIP_WEB_SERVER=1`), retain trace/video/screenshot on failure.
- 5 spec files: app boot smoke (3 tests, run today), login overlay (2 tests, run today), add-endpoint / threats-drilldown / command-palette stubbed pending an auth fixture from Phase C.

**Wiring (`Dockerfile`, `Makefile`, `install.sh`, `proxy/app_factory.py`, `.github/workflows/frontend.yml`)**
- Multi-stage Dockerfile: Node 20 builds `ui/dist/`, Python 3.12 stage `COPY --from=ui-builder` brings it in.
- `app_factory` prefers `ui/dist/` when present; fallback to source tree mounts `ui/public/*` overlays so static assets keep their pre-Vite URLs.
- Makefile: `build-ui`, `dev-ui`, `lint-ui`, `test-ui`, `e2e-ui`. `install.sh --local` builds the bundle when `npm` is available, warns and continues otherwise.
- New CI workflow `frontend.yml`: lint, typecheck, unit tests with coverage upload, build artifact, e2e against a live backend on `:8090`.

---

## [1.11.1] — 2026-04-24

### UI elevation — Sprint 2
- **Drilldown `model` kind** — `/v1/models` rows and palette jump-tos (`>model <q>`) now open an entity drawer with: overview (providers, routable-via count, recent success / error / blocked / avg-latency / cost), timeline (audit slice for that model), config (advertised-by + routing rationale), related endpoints (clickable → endpoint drilldown), actions (edit guidance + curl smoke template).
- **Drilldown `plugin` kind** — Plugin grid "Inspect" action + palette `>plugin <q>` open the same tab grammar: overview (enabled state, ring, fail policy, timeout, execution count), timeline (recent executions, fail reasons), config (entrypoint, type, runtime settings), related (other plugins in same ring), actions (toggle enable/disable, uninstall with hot-swap confirmation).
- **Command palette is now a control surface** — typing `>` enters jump-to mode: `>ep ollama`, `>model qwen`, `>plugin smart_router`, `>req <id>`. Results are pulled live from the registry and cached across palette opens. Selection routes through the drilldown system without navigating away from the current view.

### Validation
- **Backend contract tests** (`tests/test_ui_backend_contract.py`) — pin the response shape of every endpoint the UI services consume (`/api/v1/guards/status`, `/api/v1/registry`, `/v1/models`, `/api/v1/plugins`, `/api/v1/audit`). If a backend refactor drops or renames a field the UI reads, CI fails before the regression reaches prod.
- **UI service contract tests** (`tests/test_ui_service_contracts.py`) — static checks on `ui/services/*.js`: each service exports the symbols its consumers import; every relative service import resolves to a real export; key surfaces (guards, registry, models, plugins, security) actually carry `data-explain` / `data-drilldown` attributes.

### Internal
- Palette result click path distinguishes navigation commands (`view-*`) from jump-to commands (`__jump:<kind>:<id>`) so explore-style palette use stays keyboard-first.

---

## [1.11.0] — 2026-04-24

### UI elevation — operator console, Sprint 1
Lifts the frontend from "admin dashboard" to "investigation surface" by introducing three cross-cutting primitives. No new backend routes — the primitives consume data already exposed today.

**Trust by explanation (`ui/services/explain.js` + `ui/services/drawer.js`)**
- Any element carrying `data-explain="<kind>[:<id>]"` becomes a clickable/focusable affordance that opens a side drawer with the *why*: current status, source rule, timestamp, recent evidence, and a pointer to the full drilldown.
- Wired on: ASGI firewall status (`data-explain="firewall"`), per-guard cards (`data-explain="guard:injection_guard"`), per-endpoint circuit state in the registry (`data-explain="circuit:<id>"`).
- Subtle dotted-underline + ℹ glyph marks explainable surfaces without making every status badge look like a link (`style.css`).
- Keyboard-accessible: `Enter` / `Space` open the drawer on tab-focused elements, Tab traps inside the drawer, Escape closes and restores focus to the trigger.

**Universal entity drilldown (`ui/services/drilldown.js`)**
- Single tab grammar — `overview | timeline | config | related | actions` — across supported entities, so the operator doesn't relearn the UI jumping between views.
- MVP kinds: `endpoint` (from registry rows) and `request` (from audit rows). Model / plugin kinds deferred to Sprint 2.
- Registry table gains an `Inspect` action per row; Security audit table rows are now fully clickable (aria-label + role=button for screen readers) and route through drilldown.
- Related-entity navigation: request → other requests in same session, endpoint → sibling endpoints of the same type.

**Global time range (`ui/services/timerange.js`)**
- Preset selector (1h / 4h / 24h / 7d / all) in the page header context bar. Persisted in URL hash (`?tr=24h` on top of `#/audit`) and localStorage so reloads and shareable links land in the same window.
- Audit Log query now filters client-side by the active range and surfaces the label ("12 entries · Last 24 hours").
- Views without time-aware data (Threats KPI counters, Analytics) keep their own period — wiring them is a backend-side change out of scope for this ship.

### Internal
- New side-panel primitive (`ui/services/drawer.js`) shared by explain + drilldown. Same focus-trap pattern as `dialog.js` but non-modal — in-drawer links are operable without closing.
- Delegated document-level click handlers — views just stamp `data-explain` / `data-drilldown` attributes; no per-component wiring needed.

---

## [1.10.18] — 2026-04-24

### Auto-discovery
- **Periodic re-discovery of local + Tailscale peers** — a new `local_discovery_loop` runs the probe every 5 minutes (configurable via `discovery.scan_interval_s`, default 300). Peers that come back online after the boot probe are picked up automatically: no more `docker compose up --force-recreate` to notice that a Mac woke from sleep or that LM Studio was restarted. New responders are seeded into the persistence store so they show up in the UI registry on next poll. Existing entries are kept as-is — the circuit breaker already handles transient outages, and churning the registry mid-flight would race in-progress requests. Honours the same disable flags as the boot probe (`LLM_PROXY_LOCAL_DISCOVERY=0` or `discovery.local_scan: false`). Set `discovery.scan_interval_s: 0` for boot-only.

---

## [1.10.17] — 2026-04-24

### Supply chain
- **Removed dead `KNOWN_VERSIONS` coupling in `scripts/verify_deps.py`**. The dict hardcoded the same version pins already present in `requirements.txt`, so every Dependabot pip bump produced a `VERSION MISMATCH` error that failed the Supply Chain Integrity CI job — blocking every patch PR including security bumps. There was no real signal: attackers can publish tampered wheels at any version. The defense value is in the `.pth` file content scan + the `BLOCKED_PACKAGES` allow-list, both untouched. Published CVEs are caught by the existing `pip-audit` job.

---

## [1.10.16] — 2026-04-24

### UI — Security view listeners attach unconditionally
- The Security view previously wired its listeners (Audit Log query, GDPR erase/export/purge, chain verification) lazily inside `renderSecurity()`, which ran only when the store's `currentTab` transitioned TO `security`. If the user landed directly on `#/security` (reload, deep link, hash already active at boot, stale cache), that transition could be missed and the Query button silently did nothing — every click appeared inert. `security.js` now exports a real `initSecurity()` that attaches listeners eagerly on page load, matching the init/render split used by every other component. Listed in `main.js` `initWrappers` so it runs on `DOMContentLoaded` regardless of navigation state. `renderSecurity()` still calls `initSecurity()` defensively.

---

## [1.10.15] — 2026-04-24

### Routing
- **Model-aware endpoint selection** — `smart_router` now filters healthy endpoints to those that actually advertise the requested model in their catalog before scoring. Before, the router could pick any healthy endpoint at random; asking for `qwen/qwen3-4b` on a pool that included Ollama/OpenAI would route to an endpoint without that model → 502 upstream → circuit breaker churn. Endpoints with an empty `models: []` (OpenRouter, generic `openai-compatible` proxies) stay eligible as wildcards of last resort. If no endpoint advertises the model at all, the request fails fast with an actionable `no configured endpoint advertises model '<id>'` error instead of rolling the dice and waiting for a 502.

---

## [1.10.14] — 2026-04-24

### UI polish — review P2/P3 (medium + minor)
- **Command palette — no more blank-first-open** (review #10/#17). Opening the palette now pre-seeds the full command list so the first `⌘K` shows every action immediately. An empty query returns the full list; a query with no matches shows a dedicated "No matching commands" state instead of a blank panel. Query and selection reset between opens — each `⌘K` lands on a clean palette.
- **Mobile sidebar backdrop is interactive** (review #11). The full-screen dim backdrop used to be decorative; tap-outside didn't close the drawer. Now it does. `Escape` also dismisses when the drawer is open on narrow viewports.
- **No more "Loading…" purgatory** (review #12). Budget gauge, firewall stats, per-endpoint breakdown, analytics breakdowns, and the cost-efficiency card now swap their `Loading…` placeholder for a visible `role="alert"` "Unavailable — backend unreachable" state on fetch failure. Settings (system info, auth-mode, SSO status) fall back to a concrete "unavailable" / "unknown" label. A `data-ready` marker prevents the fallback from clobbering good data that was already fetched on a prior tick.
- **Settings no longer goes stale** (review #13). Clicking the Settings nav item re-runs the identity / RBAC / webhooks / export / system-info loaders so the view reflects the backend without a full page reload.
- **Log viewer stream status is visible** (review #14/#16). A new `Paused · N new` badge (wired to the existing autoscroll freeze logic) appears when the user scrolls up and disappears on click, jumping the terminal back to bottom. A status indicator (`waiting` / `connecting` / `live` / `reconnecting`) now lives in the log header, and SSE disconnects announce themselves in the terminal instead of silently retrying.
- **Cinema mode is discoverable + safer** (review #15). The shortcut was bound to plain `f`, which fired for any non-input focus — easy to hit by accident. It now requires `Shift+F`, skips `contenteditable`, and a `⇧F` `<kbd>` hint sits next to `⌘K` in the header. A brief toast confirms on/off.

---

## [1.10.13] — 2026-04-24

### UI operability (P1 from review)
- **No more silent operational failures** — the proxy-enable and priority-steering toggles in the Guards view now surface backend errors as `toast.error` instead of swallowing them into `console.error`. Success transitions also emit a confirmation toast. Combined with the earlier P0 fix to the kill switch and plugin reload/rollback, every user-initiated mutating control reports its outcome.
- **Per-field inline validation** — the Add Endpoint and Install Plugin forms now mark invalid fields with `aria-invalid="true"`, a rose border, and an inline `role="alert"` message describing the fix. On submit, focus jumps to the first invalid field. The alert clears on the next keystroke. No more guessing which input was wrong from a generic toast.
- **Registry sort is keyboard-accessible** — sortable column headers are now `<th aria-sort>` containing a proper `<button>`. Tab/Enter/Space cycle through the same sort behavior mouse users had. Screen readers announce the current sort direction.
- **Mobile tables no longer clip** — Endpoints, Models, and Analytics tables wrap in `overflow-x-auto` with a `min-width` guard so narrow viewports get horizontal scroll instead of squished unreadable columns. A proper card alternative for mobile remains a future design pass.
- **Explicit form labels** — every `<select>` and unlabelled `<input>` in the audit filter, install form, and endpoint form received a visible `<label for>` or `aria-label`. Clears the IDE accessibility diagnostics on these surfaces.
- **Button defaults** — all 29 buttons missing `type="button"` in `ui/index.html` now declare it. Prevents surprise form submissions and fixes the default-type warning.

### Notes
Review item #9 (mobile card layout) remains partial — tables scroll horizontally instead of being rewritten as cards. A dedicated design pass for a card/mobile view is tracked but out of scope here.

---

## [1.10.12] — 2026-04-24

### UI trust & operability (P0 from review)
- **Login actually validates** — the dashboard API-key login used to save any non-empty string to `localStorage` and close the overlay, surfacing auth failures only in later silent 401s. It now probes `GET /api/v1/version` with the Bearer token, shows a loading state, inline error message on invalid key, and keeps the overlay open until the backend accepts the key.
- **Login overlay is a real dialog** — added `role="dialog"`, `aria-modal="true"`, `aria-labelledby`, initial focus on the API-key field, and a keyboard focus trap. The backdrop no longer lets Tab escape into the dim-but-reachable UI underneath.
- **Chat first-run no longer dead-ends** — `ui/chat.js` previously marked the UI Offline on the initial unauthenticated `/v1/models` probe and never re-ran bootstrap after the user typed a key. Re-bootstrap is now explicit: save key → reload models → update status without a page reload.
- **In-app modal primitives** — new `ui/services/dialog.js` exposes promise-based `dialog.confirm()` and `dialog.prompt()` matching the product's glassmorphic style, with `role="dialog"`, focus trap, Escape-to-cancel, Enter-to-confirm, restored focus on close. Native `window.prompt()` / `window.confirm()` removed from chat key capture, kill switch, delete endpoint, rollback plugins, uninstall plugin, and GDPR erase.
- **Keyboard focus indicators restored** — `focus:outline-none` stripped from 22 input / select / textarea elements across `ui/index.html` and `ui/chat.html` (auth, install plugin, add endpoint, filter, GDPR, audit, chat composer). The global `*:focus-visible` rule in `style.css` now draws the 2 px Apple-blue outline on keyboard focus for the core forms.

### Internal
- `ui/chat.html` now loads `chat.js` as an ES module so it can import the shared dialog primitive.
- `ui/main.js` imports `dialog` and `toast`; the kill-switch button surfaces failures as a `toast.error` instead of swallowing them in `console.error`.

---

## [1.10.11] — 2026-04-24

### Zero-friction onboarding
- **Guided installer** — new `./install.sh` detects OS/distro, verifies Python 3.12+ and Docker Compose v2, blocks legacy `docker-compose` v1 (broken against modern urllib3), generates a random proxy auth key into `.env`, and starts the service with a post-boot health probe. Non-interactive modes: `--docker`, `--local`, `--check`, `--yes`.
- **Onboarding mode** — the proxy no longer aborts when zero providers are configured. `/health`, the admin UI, and `POST /api/v1/registry` stay reachable so the first-run wizard can complete setup from the browser. Inference requests return 503 until an endpoint is ready.
- **Boot banner** — at startup the proxy prints a copy-paste-ready summary: listening URL, active providers tagged by source (`[config]`/`[env]`/`[ui]`/`[auto-discovery]`) with model samples, WAF state + reason, auth state, and a runnable smoke-test curl using the first available model.

### Auto-discovery of local and Tailscale LLM hosts
- **Loopback probe** — new `core/local_probe.py` scans `127.0.0.1` and `host.docker.internal` on ports 11434 (Ollama), 1234 (LM Studio), 8000 (vLLM), 4000 (LiteLLM). Responders register automatically as `openai-compatible` endpoints with their real model list.
- **Remote peers** — `LLM_PROXY_DISCOVERY_PEERS=host[,host:port,...]` extends the probe to Tailscale peers, LAN nodes, or any reachable host. Discovered entries get a stable host-tagged id (`lmstudio-100-98-112-23`) so multiple peers never collide.
- **Collision-safe** — user-configured endpoints are never clobbered; discovered duplicates register as `<provider>-auto` so both remain visible.
- **Docker bridge** — `docker-compose.yml` now sets `extra_hosts: host.docker.internal:host-gateway` so Linux containers reach host-bound providers without manual setup.

### Env-based OpenAI-compatible endpoints
- New `core/env_endpoints.py` parses `LLM_PROXY_ENDPOINT_<NAME>_URL/KEY/MODELS/PROVIDER` at every config load and merges results into `config['endpoints']`. No YAML edits needed to bring up LM Studio, vLLM, TGI, Ollama, or private OpenAI-compatible APIs.

### WAF toggle
- **Env + config surface** — `LLM_PROXY_FIREWALL_ENABLED=0` or `security.firewall.enabled: false` skips the `ByteLevelFirewallMiddleware` with a visible startup warning.
- **Admin API** — `GET /api/v1/guards/status` now exposes `firewall.enabled` and `firewall.disabled_reason`.
- **UI** — Guards view reflects live WAF state with the origin of the decision (`env:…` / `config:…`). Read-only by design — no click-to-disable from the browser.

### UI improvements
- **Endpoints empty state** — redesigned as an onboarding wizard card ("Add first endpoint" CTA plus an env-var snippet) instead of a plain message.
- **Add-endpoint form** — now accepts API key (optional, held in a process-local env var) and model list. `POST /api/v1/registry` mirrors the new entry into live `config['endpoints']` so the forwarder can route to it without restart.
- **Provider dropdown** — OpenAI-compatible (local / vLLM / LM Studio) is now the first option.

### Observability hygiene
- **Health prober** — skips endpoints with unresolved placeholders in `base_url` (`{resource}`, `CHANGE-ME`) with a single informative log line at startup.
- **Rate-limited probe logs** — repeated steady-state failures are demoted to DEBUG; only state transitions (OK↔FAIL) log at WARNING. Fixes the ~20 WARN-per-minute noise on misconfigured defaults.
- **Skip open circuits** — the prober no longer hammers endpoints whose circuit breaker is open.

### Breaking-ish
- `startup_checks.validate_config` no longer raises on missing endpoints / missing provider keys — it returns warnings instead. Callers that relied on the exception (only `test_no_endpoints_raises` / `test_no_active_providers_raises` in the internal suite) have been updated to assert on the new "ONBOARDING MODE" warning text.
- `docker-compose.yml` dropped the obsolete `version:` key (Compose v2 emits a warning). An explicit comment forbids legacy `docker-compose` v1 usage.

---

## [1.10.8] — 2026-03-30

### UI: Full Gap Analysis Closure
- **Cost Efficiency table** — model ranking by avg cost/request, cheapest/most expensive highlighted
- **Webhook Test Fire** — button in Settings sends test payload to all configured endpoints
- **Uptime + Pool Health KPIs** — Threats dashboard shows server uptime and healthy/total endpoints
- All "trivial" and "moderate" backend-to-UI gaps from the capability audit are now closed

### Cumulative Session Stats (v1.10.2 - v1.10.8)
- 7 releases, 30+ commits
- 76 security findings fixed (red team round 1 + 2, WAF audit)
- Adaptive firewall (162 signatures, 157 semantic patterns, confidence scoring, AI escalation)
- Complete SOC UI (chat, operations panel, audit query, GDPR controls, endpoint management)
- 942/942 tests passing

---

## [1.10.7] — 2026-03-30

### UI: Security Operations
- **Audit Log Query** — Filterable table in Security view (model, key prefix, blocked/passed, limit). Shows timestamp, model, HTTP status, token counts, cost, blocked indicator.
- **GDPR Erase (Art. 17)** — Delete all data for a subject with confirmation dialog.
- **GDPR Purge Expired** — Trigger retention purge from UI.
- **Add Endpoint** — Form in Endpoints view (name, URL, provider dropdown, priority). New `POST /api/v1/registry` backend endpoint.
- **Nav font size** — 11px to 13px for readability.

### Stats
- 942/942 tests passing

---

## [1.10.6] — 2026-03-30

### Adaptive Firewall
- **External signature loading** — Signatures and injection corpus loaded from `data/signatures.yaml` and `data/injection_corpus.yaml`. Hot-reloaded every 30s. Falls back to hardcoded if files missing.
- **Confidence-based escalation** — Composite score (0.4*regex + 0.35*semantic + 0.25*trajectory). Block >= 0.7, pass <= 0.3, gray zone escalates to AI.
- **On-demand AI analysis** — Gray-zone requests (~2% of traffic) analyzed by `assistant.generate()` with 5s timeout. Fail-closed. No AI = threshold fallback.
- **162 firewall signatures** (was 28), **157 semantic patterns** (was 64), **11 categories**, **20+ languages**.

### Critical Fixes
- **Forwarder missing provider API key** — Upstream requests sent without Authorization header. All providers failed silently. Fixed: inject from endpoint config `api_key_env`.
- **Model resolver group/endpoint mismatch** — "auto" picked `gpt-4o-mini` but sent to Google. Fixed: `resolve_model()` returns `(model, provider)`, smart router pins endpoint.
- **Startup rejected real API keys** — `startswith("AIza...")` matched all Google keys. Fixed: exact-match against placeholder set.
- **Speculative guardrail PII false positive** — Aborted streams on legitimate content (model names matched PII regex). Removed PII check from speculative guardrail.
- **OTEL console span flood** — `ConsoleSpanExporter` default `True` dumped JSON spans to stdout. Default changed to `False`.
- **SSE auth** — `EventSource` can't send headers. Added `?token=` query param fallback. Deferred connection until token available in localStorage.
- **CSP blocked Tailwind JIT** — Added `'unsafe-inline'` for styles, `'unsafe-eval'` for scripts. Removed Google Fonts refs (fonts are local).

### Operations Panel (Guards View)
- **Reset WAF Counters** — `POST /api/v1/firewall/reset`
- **Clear Caches** — `POST /api/v1/cache/clear` (L1 negative + L2 positive)
- **Reset Sessions & Ledger** — `POST /api/v1/security/reset`
- **Circuit Breaker Reset** — `POST /api/v1/circuit-breaker/{id}/reset` + per-endpoint UI button
- **Config Reload** — existing `POST /api/v1/admin/reload` exposed in UI

### Chat Interface (`/ui/chat.html`)
- Streaming chat with model selector (auto-populated from `/v1/models`)
- Safe markdown rendering (escapeHtml first, then regex formatting — no XSS)
- Provider + model labels: `groq (llama-3.3-70b-versatile)`
- Per-message stats: TTFT, TPS (tok/s), prompt/completion tokens, total time
- Multi-turn conversation history
- Error display for upstream provider failures

### Models & Config
- Updated all providers to March 2026 models (OpenAI gpt-5.4, Anthropic claude-opus-4-6, Google gemini-3.1)
- Resilient "auto" group: filters to available providers only
- Endpoint seeding: config.yaml endpoints auto-registered in DB at startup
- SSE shutdown: `CancelledError` caught in generators for clean Ctrl+C

### UI
- Eye favicon + logo (SVG, consistent brand)
- README rewritten: 780 -> 285 lines

### Stats
- 942/942 tests passing
- 33 files changed, +2198 -150 lines since v1.10.5
- 21 commits

---

## [1.10.5] — 2026-03-30

### Red Team Round 2 — 12 new findings fixed (2 CRITICAL, 6 HIGH, 4 MEDIUM)

**CRITICAL:**
- **Rate limiter global lock starvation** (R2-01) — H4 fix from Round 1 moved `bucket.acquire()` inside global lock, serializing ALL rate checks. Reverted: acquire outside lock, orphan bucket = 1 free token (acceptable).
- **JWT roles claim injection** (R2-02) — `_resolve_roles()` trusted raw JWT `roles` claim. Attacker with own OIDC tenant could set `roles: ["admin"]`. Fixed: validate against `DEFAULT_PERMISSIONS` keys.

**HIGH:**
- **`<s>` signature false positive** (R2-03) — 3-byte `<s>` matched all HTML `<strong>`, `<span>`, etc. Changed to `<s>[inst]` (Llama-2 specific).
- **Cyrillic homoglyph bypass** (R2-06) — NFKC doesn't map Cyrillic а/е/о to Latin. Added `_CONFUSABLE_MAP` (17 Cyrillic+Greek homoglyphs) in both firewall and semantic analyzer.
- **ThreatLedger unbounded list OOM** (R2-07) — 1000 req/s × 600s = 600k tuples per actor. Capped at 1000 entries per actor.
- **Firewall counter race** (R2-08) — Class-level `+=` not atomic. Documented as approximate metrics (lock would add latency).

**MEDIUM:**
- **Audit + analytics endpoints missing auth** (R2-10/11) — `_check_admin_auth()` added to `/api/v1/audit`, `/api/v1/analytics/spend`, `topmodels`, `cost-efficiency`.
- **Audit verify OOM** (R2-12) — `SELECT * FROM audit_log` loaded entire table. Added `LIMIT 100000`.
- **row_factory cancellation** (R2-15) — Added `try/finally` to all 4 `_row_factory_lock` blocks.
- **GDPR export IDOR** (R2-09) — Short subjects (e.g., "a") matched broadly. Added 8-char minimum.

### Stats
- 915/915 tests passing
- 12 files changed
- 0 regressions

---

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
