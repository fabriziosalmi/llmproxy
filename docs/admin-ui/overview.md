# Admin UI

The admin UI is a browser application served by the proxy at `http://localhost:8090/ui`. It is written in JavaScript and TypeScript without a framework and uses Tailwind CSS, Chart.js and xterm.js. The scripts and fonts are served from the proxy itself (`ui/public/vendor/`); the page loads nothing from a CDN.

The proxy serves the built assets from `ui/dist/` when that directory exists, and the source tree otherwise.

## Signing in

A sign-in overlay asks for an API key and checks it against `GET /api/v1/identity/me`. When SSO providers are configured, the overlay also offers them. The key is kept in the browser's `localStorage` and sent as a bearer token on every call. The Home screen reads `/metrics`, which requires an admin key.

## Screens

The sidebar has ten entries.

| Screen | Content |
|--------|---------|
| [Home](/admin-ui/threats) | Counters, budget, firewall statistics, ring latency, security event feed. Its page title is "Threats" |
| [Guards](/admin-ui/guards) | Proxy on/off switch, priority steering, guard switches, cache statistics, reset actions |
| [Plugins](/admin-ui/plugins) | A card for each loaded plugin, with statistics and actions |
| [Models](/admin-ui/models) | The models returned by `/v1/models` |
| [Analytics](/admin-ui/analytics) | Spend by model and by provider |
| Security | Audit chain check, GDPR export and erase, the semantic pattern list, audit log query |
| [Endpoints](/admin-ui/endpoints) | The endpoint registry, with test, toggle and delete actions |
| [Live Logs](/admin-ui/logs) | A terminal fed by the log stream |
| Settings | Configuration (guided editor and raw YAML), access and identity, rate limits and routing, webhooks and API reference, version, health and data export, appearance |
| Docs | A short built-in help page |

The screen is selected by the URL fragment, for example `/ui/#/endpoints`.

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Cmd+K` / `Ctrl+K` | Open or close the command palette |
| `Esc` | Close the command palette |
| `Shift+F` | Cinema mode on or off (ignored while typing in a field) |

The command palette filters its commands by substring. Typing `>` switches it to a lookup of endpoints (`>ep`), models (`>model`), plugins (`>plugin`) and request ids (`>req`).

## Other controls

- **Updates.** The screens poll the API: every 10 seconds on Home and Guards, every 30 seconds on Plugins, Models and Analytics and for the endpoint registry. The security event feed and Live Logs read one server-sent event stream, `/api/v1/logs`.
- **Status indicator.** Every 5 seconds the page calls `/api/v1/proxy/status` and shows `Live` or `Offline`.
- **Kill switch.** The button at the bottom of the sidebar asks for confirmation and then calls `POST /api/v1/panic`, which disables the proxy.
- **Theme.** A header button switches between the dark and the light theme.
- **Density and time range.** The header has an Overview / Investigate switch and a time-range selector.
- **Layout.** The sidebar collapses, and there is a menu button for narrow screens.

## Known limitations

- The Settings screen has "Audit trail" and "Mask PII in audit log" switches bound to `logging.audit_trail.enabled` and `logging.audit_trail.mask_pii`. No backend code reads these keys. The switches change the file and nothing else.
- A configuration change that lowers the security posture cannot be applied from the UI. The backend asks for a confirm token for such a change (`proxy/routes/config.py`), and the UI sends only the YAML.
- The "Verify Chain" button on the Security screen calls `GET /api/v1/audit/verify` without an anchor.
- The Plugins screen lists loaded plugins only (see [Plugins](/admin-ui/plugins)).
- The "PII Masked" counter on Home does not count masked PII (see [Home](/admin-ui/threats)).
- The header labels `ENV: PROD`, `SCOPE: ADMIN` and `WORKSPACE: DEFAULT` are fixed text.
- `ui/chat.html` is built and served at `/ui/chat.html`, but no screen links to it.
