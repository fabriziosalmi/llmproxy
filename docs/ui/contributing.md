# UI Contributing Guide

> Audience: anyone touching the admin console (`ui/`). Covers the build pipeline, the primitive system, the C.2 view-migration pattern, and the test contract. Backend contribution rules live in [`CONTRIBUTING.md`](../../CONTRIBUTING.md) at the repo root.

## TL;DR

```bash
make build-ui     # Install npm deps + Vite production build (writes ui/dist/)
make dev-ui       # Vite dev server with HMR on :5173 (proxies API to :8090)
make test-ui      # Vitest unit suite (fast, ~2s)
make e2e-ui       # Playwright e2e (auto-starts the backend on :8090)
make lint-ui      # ESLint + Prettier check
```

CI runs the same four checks on every PR via `.github/workflows/frontend.yml`. Lint and Prettier are zero-warning.

## Architecture

```
ui/
├── index.html            # admin console entry point (Vite-bundled)
├── chat.html             # chat surface entry point
├── main.js               # legacy boot shell — wires up tabs, login, palette
├── components/           # legacy tab shells (one per view, *.js)
├── services/             # cross-cutting helpers (api, store, drilldown,
│                         # explain, timerange, toast)
├── public/               # static assets (chart.min.js, xterm, fonts)
└── src/                  # ⭐ all new code lives here
    ├── ui/               # primitive components (TS, framework-free)
    ├── views/            # one folder per migrated tab (TS)
    ├── services/         # TS services (logger, rum)
    └── dev/              # Storybook-lite gallery
```

Two layers, on purpose:

- `components/*.js` and `services/*.js` are the **legacy boot shell**. They render the raw HTML in `index.html` so the page works even without a Vite build (source-tree fallback). They never accumulate new logic — every new feature ships in `src/`.
- `src/views/<tab>/` holds the **TypeScript view**. The legacy shell dynamic-imports it (`import('../src/views/<tab>/index')`) and the TS view replaces the legacy markup via `replaceChildren`. A `_tsMounted` flag in the legacy shell stops legacy renderers from clobbering TS state on subsequent store updates.

This is a **strangler-fig migration**: legacy stays for fallback, TS owns the runtime.

## Adding a new primitive

Primitives live in `ui/src/ui/`. Pattern:

```ts
// ui/src/ui/MyThing.ts
import { cx } from './classnames';

export interface MyThingOptions {
    label: string;
    onClick?: (ev: MouseEvent) => void;
    testId?: string;
    className?: string;
}

export function createMyThing(opts: MyThingOptions): HTMLElement {
    const root = document.createElement('button');
    root.className = cx('inline-flex …', opts.className);
    root.textContent = opts.label;
    if (opts.onClick) root.addEventListener('click', opts.onClick);
    if (opts.testId) root.setAttribute('data-testid', opts.testId);
    return root;
}
```

Rules:

1. **Factory function returning an `HTMLElement`.** No virtual DOM, no framework. Tests render directly with happy-dom.
2. **Options object** with `testId?` and `className?` extension hooks. The `testId` lands on the most-interactive element (the `<button>` for Toggle, not the wrapper `<div>`).
3. **Uses other primitives** from `./` (e.g. Card composes Button) — never reach across to `views/`.
4. **ARIA done right.** Buttons use native `<button>`, switches use `role="switch"` + `aria-checked`, modals use `role="dialog"` + `aria-modal`. Test the ARIA in the unit suite.
5. **Tailwind utilities directly.** No styled-components. The `tailwind.config.js` content scanner reads the .ts files; literal class strings only (no template-string interpolation that would defeat the scanner).
6. **Add a story.** Drop a variant or three into `ui/src/dev/stories.ts` so the gallery covers the new primitive. View it with `make dev-ui` then `http://localhost:5173/ui/dev/primitives.html`.
7. **Export from the barrel** `ui/src/ui/index.ts` so callers import via `from '../../ui'` not the deep path.
8. **Test it.** Co-located `MyThing.test.ts` with at least: render contract, callback wiring, ARIA flips, `disabled` semantics if applicable.

## Adding a new view (or migrating a legacy tab)

The pattern was crystallised in Phase C.2 (Threats) and validated five more times in Phase F+G. Six tabs migrated end-to-end follow this template.

### 1. Audit the legacy view

Read `ui/components/<tab>.js` and the matching `<div id="view-<tab>">` block in `ui/index.html`. Identify:

- The data sources (which `api.fetch*` calls, which store fields).
- The render units (cards, tables, kpi tiles, …) — these become individual `src/views/<tab>/*.ts` files.
- Any actions (buttons, forms) — these get factored into typed deps.

### 2. Build the TS view skeleton

```
ui/src/views/<tab>/
├── types.ts      # Backend response shapes + view types
├── <Section>.ts  # One file per render unit (Kpi, Form, Table, …)
├── <Section>.test.ts
└── index.ts      # Orchestrator: mount<Tab>View(hosts, opts)
```

The orchestrator:

```ts
export function mount<Tab>View(hosts: <Tab>Hosts, opts: Mount<Tab>Options): () => void {
    if (!hosts.someRequiredHost) return () => {};

    // Mount each section into its host (replaceChildren)
    const list = createList(...);
    hosts.list.replaceChildren(list.root);

    async function refresh(): Promise<void> {
        const data = await opts.api.fetchSomething();
        list.setData(data);
    }

    void refresh();
    const stopPoll = opts.poll
        ? opts.poll(refresh, opts.pollIntervalMs ?? 10_000)
        : (() => { const id = setInterval(refresh, 10_000); return () => clearInterval(id); })();

    return stopPoll;
}
```

### 3. Wire the markup mount points

In `ui/index.html`, wrap each section the TS view will own in a `<div id="<tab>-<section>-host">…</div>`. The legacy markup stays inside the wrapper as the source-tree fallback. Example from Endpoints:

```html
<div id="add-endpoint-form-host" data-testid="add-endpoint-form-host">
    <div id="add-endpoint-form" class="hidden …">
        <!-- legacy form fallback — TS view replaceChildren()s this away -->
    </div>
</div>
```

### 4. Delegate from the legacy shell

In `ui/components/<tab>.js`:

```js
let _tsMounted = false;

export function init<Tab>() {
    // …legacy initialisation (still runs for the source-tree fallback)…

    import('../src/views/<tab>/index')   // ← bare path; Vite resolves to .ts at build
        .then(({ mount<Tab>View }) => {
            _tsMounted = true;
            mount<Tab>View(
                { /* hosts: document.getElementById(...) for each */ },
                { api: { /* proxy api.* methods */ }, toast,
                  poll: (fn, ms) => store.poll(fn, ms, '<tab>') },
            );
        })
        .catch(() => { /* no Vite build — legacy stays live */ });
}

export function render<Tab>() {
    if (_tsMounted) return;        // ← prevent legacy from clobbering TS state
    // …legacy render path…
}
```

Critical: every legacy renderer that the store can re-trigger needs the early `if (_tsMounted) return;` bail. Forgetting it means `store.update(...)` re-runs the legacy renderer over the TS DOM and the user sees flicker / regressions.

### 5. Tests

- **Unit (Vitest, happy-dom)**: per section, exercise rendering + callbacks + state transitions. Use `data-testid` attributes for stable selectors. ~5-10 tests per non-trivial section.
- **E2E (Playwright)**: one `e2e/<NN>-<tab>.spec.ts` covering the operator's main flow. Stub backend routes via `page.route()` so tests are deterministic. Use the auth fixture (`e2e/fixtures/auth.ts`) and any other shared fixture before rolling your own.

Target: 5-10 unit tests + 4-6 e2e tests per migrated view.

### 6. Bump the version

Each migrated view ships a minor (`1.x.0`) bump. Add a `CHANGELOG.md` entry under "Operator console — `<Tab>` vertical slice".

## Coding conventions

- **TypeScript medium-strict** (`tsconfig.json`). New code is fully typed. Legacy `.js` is untyped (`checkJs: false`) but isolated.
- **No barrel re-exports across boundaries.** A view imports primitives from `'../../ui'`; primitives never import from views.
- **`cx()` for class composition.** Do not template-string-interpolate Tailwind classes — the content scanner can't see them.
- **Dynamic import for legacy → TS.** Always `import('../src/views/.../index')` (bare, no extension). That resolves to `.ts` at build time and 404s in source-tree fallback (which is what we want — `.catch()` keeps the legacy shell live).
- **`testId` attribute** on every interactive element. Format: `<context>-<action>-<id>` (e.g. `ep-delete-flaky`).
- **Commit conventions**: `feat(ui):`, `fix(ui):`, `chore(ui):`. One commit per sub-phase, separate `chore` commit for VERSION + CHANGELOG bumps.

## Test patterns

### Unit: render contract

```ts
import { describe, expect, it, vi } from 'vitest';
import { createMyThing } from './MyThing';

describe('MyThing', () => {
    it('renders the label and wires onClick', () => {
        const onClick = vi.fn();
        const el = createMyThing({ label: 'Save', onClick });
        expect(el.textContent).toBe('Save');
        el.click();
        expect(onClick).toHaveBeenCalledTimes(1);
    });
});
```

### Unit: state-machine view section

```ts
it('falls back to ErrorState when the API rejects', async () => {
    const handle = mountSection(host, { fetch: vi.fn().mockRejectedValue(new Error('500')) });
    await handle.refresh();
    const err = host.querySelector('[data-testid="section-error"]')!;
    expect(err.querySelector('[data-testid="error-state-retry"]')).not.toBeNull();
});
```

### E2E: stub the backend, drive the UI

```ts
import { test, expect } from './fixtures/auth';

test('Settings → Identity surfaces the authenticated user', async ({ authedPage }) => {
    await authedPage.route('**/api/v1/identity/me', async (route) => {
        await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({ authenticated: true, email: 'fab@example.com', roles: ['admin'] }),
        });
    });
    await authedPage.goto('/ui/#/settings');
    await expect(authedPage.locator('[data-testid="settings-identity"]')).toContainText('fab@example.com');
});
```

## Common pitfalls

- **Forgetting the `_tsMounted` bail in a legacy renderer.** Symptom: the TS-rendered DOM gets overwritten when the store changes. Fix: add the early return.
- **Hard-coded literal Tailwind class with template-string interpolation.** Symptom: class disappears at build time, unstyled element. Fix: use literal strings + `cx()` or a `const COLOR_BY_X: Record<X, string>` map.
- **Importing a `.ts` file with explicit `.ts` extension.** Symptom: build error. Fix: use the bare path.
- **Missing the `keepalive: true` on backend log POSTs.** Symptom: in-flight log batches lost on tab close. Fix: see `backendSink` in `src/services/logger.ts`.

## Storybook-lite gallery

```bash
make dev-ui
open http://localhost:5173/ui/dev/primitives.html
```

The gallery is dev-only; never bundled in `dist/`. Add a story when you ship a new primitive variant by extending `ui/src/dev/stories.ts`. Stories are typed (`Story` interface), grouped automatically by `primitive`.

## Pull-request checklist (UI)

- [ ] `make lint-ui` clean (zero warnings)
- [ ] `make test-ui` passes
- [ ] `make e2e-ui` passes (or stubs new backend interactions)
- [ ] `make build-ui` produces a build (no Rollup errors)
- [ ] New primitives have a story in `ui/src/dev/stories.ts`
- [ ] CHANGELOG entry added
- [ ] `data-testid` on every interactive surface that an e2e or unit test references
- [ ] No fresh `.js` files in `components/` or `services/` — new code goes in `src/`

## Where things live (cheat sheet)

| You want to add… | Path |
|---|---|
| A new primitive | `ui/src/ui/<Name>.ts` + barrel re-export in `ui/src/ui/index.ts` |
| A new view (tab migration) | `ui/src/views/<tab>/` + delegation from `ui/components/<tab>.js` |
| A new API method | `ui/services/api.js` (legacy) — typed signatures end up in the per-view `types.ts` |
| A test fixture | `ui/e2e/fixtures/` |
| A story | `ui/src/dev/stories.ts` |
| A primitive variant for Storybook-lite | `ui/src/dev/stories.ts` |
| A new backend endpoint surfaced in the UI | `proxy/routes/` (Python) + `ui/services/api.js` + per-view consumer |
