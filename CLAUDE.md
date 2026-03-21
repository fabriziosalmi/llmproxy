# CLAUDE.md — Project Context for Claude Code

## What is this project?
**LLMProxy** is an LLM Security Gateway — a security-first proxy for Large Language Models with a ring-based plugin pipeline, WASM-sandboxed plugin execution, NLP-powered PII detection, and a real-time Security Operations Center UI.

## Tech Stack
- **Backend**: Python 3.12+ / FastAPI / uvicorn / aiohttp
- **Frontend**: Vanilla JS (ES Modules) + Tailwind CSS CDN + xterm.js + Chart.js — served from `/ui/`
- **Database**: SQLite (aiosqlite)
- **Tests**: pytest + pytest-asyncio (use `python3.14 -m pytest` if default `pytest` not found)

## Project Structure
```
llmproxy/
├── main.py                  # Entry point (uvicorn)
├── proxy/rotator.py         # Security gateway orchestrator (~230 lines)
├── proxy/routes/             # Modular route handlers (chat, admin, registry, identity, plugins, telemetry)
├── core/
│   ├── plugin_engine.py     # Ring-based dual-mode plugin engine (PluginManager, PluginContext, PluginState)
│   ├── plugin_sdk.py        # BasePlugin, PluginResponse, PluginHook, PluginAction
│   ├── security.py          # SecurityShield (injection scoring, Presidio NLP + regex PII, trajectory detection)
│   ├── wasm_runner.py       # WASM/Rust plugin runner via Extism
│   ├── firewall_asgi.py     # Byte-level ASGI firewall middleware
│   ├── rate_limiter.py      # Per-IP/per-key token bucket ASGI middleware
│   ├── identity.py          # OIDC/JWT multi-provider SSO
│   ├── rbac.py              # Role-based access control
│   ├── webhooks.py          # Slack/Teams/Discord webhook dispatcher
│   └── export.py            # JSONL dataset export with PII scrubbing
├── plugins/
│   ├── manifest.yaml        # Plugin registry (11 plugins)
│   ├── marketplace/         # BasePlugin class plugins (loop_breaker, budget_guard)
│   └── wasm/                # WASM plugin templates + README
├── ui/                      # Security Operations Center UI (vanilla JS, served at /ui/)
│   ├── index.html           # SOC single-page app (Threats, Guards, Plugins, Endpoints, Audit Log, Settings)
│   ├── components/          # JS modules: threats.js, guards.js, logs.js, plugins.js, settings.js, registry.js
│   └── services/            # api.js (centralized fetch), store.js (state), auth.js (OAuth)
├── tests/                   # pytest suite (158 tests)
├── config.yaml              # Main configuration
├── OGGI.md                  # Italian work log (session-based changelog)
└── README.md                # Full documentation
```

## Key Architectural Decisions
1. **Security thesis**: "LLM proxy with the best security plugin system" — all non-security code has been cut.
2. **Plugin engine is ring-based**: 5 rings (INGRESS → PRE_FLIGHT → ROUTING → POST_FLIGHT → BACKGROUND). Supports both legacy functions and BasePlugin classes.
3. **SecurityShield.inspect()** runs pre-INGRESS in `rotator.py` — first line of defense after the ASGI firewall.
4. **PluginState DI**: Shared mutable state (cache, metrics, config) injected into every PluginContext.
5. **View routing convention**: `ui/components/content.js` uses `document.getElementById('view-${tabName}')` — all view IDs must follow `view-{name}` pattern.
6. **API centralization**: All fetch calls go through `ui/services/api.js` — never use raw `fetch()` in components.
7. **App factory pattern**: `create_app(agent) -> FastAPI` in `proxy/rotator.py`.

## Running Tests
```bash
# Core plugin/WASM tests (54 tests, always pass)
python3.14 -m pytest tests/test_marketplace_plugins.py tests/test_wasm_runner.py tests/test_plugin_engine.py -v

# Full suite (449 tests — all pass)
python3.14 -m pytest tests/ -v --ignore=tests/test_store.py --ignore=tests/integrated_test.py --ignore=tests/test_export.py
```

## Known Constraints
- `tests/test_store.py` requires `aiosqlite` (not installed in dev venv)
- `tests/integrated_test.py` requires `pytest-asyncio` compatible with Python 3.14
- `tests/test_export.py` requires `aiofiles` — in requirements.txt but may not be in dev venv; install with `pip install aiofiles`
- PII detection: Presidio NLP opt-in (requires `presidio-analyzer`), regex fallback always available
- `cachetools` required for NegativeCache and TTL-bounded pii_vault — in requirements.txt, install with `pip install cachetools`

## Common Pitfalls
- **HTML index.html**: Views are nested divs with `hidden` class. Nesting bugs cause entire views to disappear — always verify tag balance.
- **Plugin timeout defaults**: 500ms for legacy functions, 50ms for BasePlugin classes.
- **AST scanner**: Blocks `os`, `subprocess`, `socket`, `exec`, `eval` in plugins — this is intentional security.
- **Font stack**: Terminal uses `'JetBrains Mono', 'Fira Code', monospace` (JetBrains Mono is primary).
- **UI views**: threats (default), guards, plugins, endpoints, logs, settings — nav IDs follow `nav-{name}`.

## Style
- Backend: Python, type hints encouraged, async/await everywhere
- Frontend: Vanilla JS ES Modules, Tailwind CSS utility classes, glassmorphism dark theme (rose accent for security)
- Commits: descriptive, in English
- OGGI.md: session-based work log, in Italian
