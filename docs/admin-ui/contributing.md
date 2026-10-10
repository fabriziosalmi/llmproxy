# Contributing to the Admin UI

> Audience: anyone changing the admin UI (`ui/`). Covers the build, the primitive components, the pattern for moving a view to TypeScript, and the tests. Backend contribution rules live in [`CONTRIBUTING.md`](https://github.com/fabriziosalmi/llmproxy/blob/main/CONTRIBUTING.md) at the repo root.

## TL;DR

```bash
make build-ui     # Install npm deps + Vite production build (writes ui/dist/)
make dev-ui       # Vite dev server with HMR on :5173 (proxies API to :8090)
make test-ui      # Vitest unit suite
make e2e-ui       # Playwright e2e against the backend on :8090
make lint-ui      # ESLint + Prettier check
```

`make e2e-ui` starts the backend itself (`python main.py` from the repository's `venv`) unless, outside CI, one is already answering on `:8090`. Set `LLMPROXY_SKIP_WEB_SERVER=1` to stop it from doing so.

`.github/workflows/frontend.yml` runs ESLint, the Prettier check, the type check, the unit tests with coverage, `npm audit`, the build and the e2e suite. It runs on pushes to `main` and on pull requests that change `ui/**`, `proxy/app_factory.py` or the workflow file; other changes do not trigger it. ESLint runs with `--max-warnings=0`.

## Architecture

```
ui/
├── index.html            # admin UI entry point (Vite input)
├── chat.html             # chat page (Vite input; no screen links to it)
├── oauth-callback.html   # SSO callback page (Vite input)
├── main.js               # boot shell: tabs, login, command palette
├── components/           # one shell per view (*.js)
├── services/             # JS helpers (api, auth, file_actions, store,
│                         # timerange, toast, urlstate)
├── public/vendor/        # static assets (chart.min.js, xterm, fonts)
├── e2e/                  # Playwright specs and fixtures
└── src/                  # new code goes here
    ├── ui/               # primitive components (TS, no framework)
    ├── views/            # one folder per view written in TS
    ├── services/         # TS services (drilldown, explain, logger,
    │                     # perf, rum, theme)
    └── dev/              # primitives gallery
```

Two layers, on purpose:

- `components/*.js` and `services/*.js` are the **boot shell**. They drive the markup in `index.html`, so the page works without a Vite build: the proxy serves `ui/dist/` when it exists and the source tree otherwise. New code goes in `src/`.
- `src/views/<tab>/` holds the **TypeScript view**. The legacy shell dynamic-imports it (`import('../src/views/<tab>/index')`) and the TS view replaces the legacy markup via `replaceChildren`. A `_tsMounted` flag in the legacy shell stops legacy renderers from clobbering TS state on subsequent store updates.

With a build, the TypeScript view is what runs; the shell's own renderers are the fallback.

Seven views have a TypeScript implementation in `src/views/`: threats, guards, plugins, endpoints, models, security and settings. Security is imported directly by `main.js`; the other six are loaded through their shell in `components/`. Analytics and Live Logs exist only as `components/analytics.js` and `components/logs.js`.

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
4. **ARIA.** Buttons use native `<button>`, switches use `role="switch"` + `aria-checked`, modals use `role="dialog"` + `aria-modal`. Test the ARIA in the unit suite.
5. **Tailwind utilities directly.** No styled-components. The `tailwind.config.js` content scanner reads the .ts files; literal class strings only (no template-string interpolation that would defeat the scanner).
6. **Add a story.** Drop a variant or three into `ui/src/dev/stories.ts` so the gallery covers the new primitive. View it with `make dev-ui` then `http://localhost:5173/ui/dev/primitives.html`.
7. **Export from the barrel** `ui/src/ui/index.ts` so callers import via `from '../../ui'` not the deep path.
8. **Test it.** Co-located `MyThing.test.ts` with at least: render contract, callback wiring, ARIA flips, `disabled` semantics if applicable.

## Adding a new view (or migrating a legacy tab)

The views that are loaded through a shell in `components/` follow this template.

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

- **Unit (Vitest, happy-dom)**: per section, exercise rendering, callbacks and state transitions. Use `data-testid` attributes for stable selectors.
- **E2E (Playwright)**: one `e2e/<NN>-<tab>.spec.ts` covering the operator's main flow. Stub backend routes via `page.route()` so tests are deterministic. Use the auth fixture (`e2e/fixtures/auth.ts`) and any other shared fixture before rolling your own.

### 6. Version and changelog

Update `VERSION` and add a `CHANGELOG.md` entry in the same change.

## Coding conventions

- **TypeScript** with `strict: true` and `noImplicitAny: false` (`tsconfig.json`). `.js` files are not type-checked (`checkJs: false`).
- **No barrel re-exports across boundaries.** A view imports primitives from `'../../ui'`; primitives never import from views.
- **`cx()` for class composition.** Do not template-string-interpolate Tailwind classes — the content scanner can't see them.
- **Dynamic import for legacy → TS.** Always `import('../src/views/.../index')` (bare, no extension). That resolves to `.ts` at build time and 404s in source-tree fallback (which is what we want — `.catch()` keeps the legacy shell live).
- **`testId` attribute** on every interactive element. Format: `<context>-<action>-<id>` (e.g. `ep-delete-flaky`).
- **Commit messages** use a type and a scope: `feat(ui):`, `fix(ui):`, `chore(ui):`.

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

## Primitives gallery

```bash
make dev-ui
open http://localhost:5173/ui/dev/primitives.html
```

The gallery is served by the dev server only. It is not one of the build's inputs, so it is not in `dist/`. Add a story when you ship a new primitive variant by extending `ui/src/dev/stories.ts`. Stories are typed (`Story` interface), grouped automatically by `primitive`.

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
| A new API method | `ui/services/api.js`; the typed signatures go in the view's `types.ts` |
| A test fixture | `ui/e2e/fixtures/` |
| A story | `ui/src/dev/stories.ts` |
| A primitive variant for the gallery | `ui/src/dev/stories.ts` |
| A new backend endpoint surfaced in the UI | `proxy/routes/` (Python) + `ui/services/api.js` + per-view consumer |
