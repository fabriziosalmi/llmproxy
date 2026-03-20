# CLAUDE.md — Project Context for Claude Code

## What is this project?
**LLMProxy** is a universal AI gateway/proxy — an aggregator, load balancer, and autonomous discovery engine for Large Language Models. It provides a unified OpenAI-compatible API, security hardening, plugin engine, and a real-time dashboard UI.

## Tech Stack
- **Backend**: Python 3.12+ / FastAPI / uvicorn / aiohttp
- **Frontend**: Vanilla JS (ES Modules) + Tailwind CSS CDN + xterm.js + Chart.js — served from `/ui/`
- **Database**: SQLite (aiosqlite) + ChromaDB (vector search)
- **Tests**: pytest + pytest-asyncio (use `python3.14 -m pytest` if default `pytest` not found)

## Project Structure
```
llmproxy/
├── main.py                  # Entry point (uvicorn)
├── proxy/rotator.py         # Main request router (770+ lines, FastAPI routes + RotatorAgent)
├── core/
│   ├── plugin_engine.py     # Ring-based dual-mode plugin engine (PluginManager, PluginContext, PluginState)
│   ├── plugin_sdk.py        # BasePlugin, PluginResponse, PluginHook, PluginAction
│   ├── security.py          # SecurityShield (injection scoring, PII masking, trajectory detection)
│   ├── wasm_runner.py       # WASM/Rust plugin runner via Extism
│   ├── firewall_asgi.py     # Byte-level ASGI firewall middleware
│   ├── identity.py          # OIDC/JWT multi-provider SSO
│   ├── rbac.py              # Role-based access control
│   ├── webhooks.py          # Slack/Teams/Discord webhook dispatcher
│   ├── chatops.py           # Telegram bot + HITL
│   ├── export.py            # JSONL dataset export with PII scrubbing
│   └── semantic_cache.py    # ChromaDB semantic caching
├── agents/                  # Autonomous agent swarm (scanner, validator, self-healer, SOTA)
├── plugins/
│   ├── manifest.yaml        # Plugin registry (11 plugins)
│   ├── marketplace/         # BasePlugin class plugins (loop_breaker, budget_guard)
│   └── wasm/                # WASM plugin templates + README
├── ui/                      # LIVE frontend (vanilla JS, served at /ui/)
│   ├── index.html           # Single-page app (all views in one file)
│   ├── components/          # JS modules: proxy.js, logs.js, chat.js, settings.js, plugins.js, ...
│   └── services/            # api.js (centralized fetch), store.js (state), auth.js (OAuth)
├── frontend/                # React app (NOT served, NOT the live UI — ignore)
├── tests/                   # pytest suite
├── config.yaml              # Main configuration
├── OGGI.md                  # Italian work log (session-based changelog)
└── README.md                # Full documentation
```

## Key Architectural Decisions
1. **Dual frontend**: `/ui/` is the LIVE vanilla JS frontend. `/frontend/` is an unused React app — do NOT modify it.
2. **Plugin engine is ring-based**: 5 rings (INGRESS → PRE_FLIGHT → ROUTING → POST_FLIGHT → BACKGROUND). Supports both legacy functions and BasePlugin classes.
3. **SecurityShield.inspect()** runs pre-INGRESS in `rotator.py` — it's the first line of defense after the ASGI firewall.
4. **PluginState DI**: Shared mutable state (cache, metrics, config) injected into every PluginContext.
5. **View routing convention**: `ui/components/content.js` uses `document.getElementById('view-${tabName}')` — all view IDs must follow `view-{name}` pattern.
6. **API centralization**: All fetch calls go through `ui/services/api.js` — never use raw `fetch()` in components.

## Running Tests
```bash
# Core plugin/WASM tests (46 tests, always pass)
python3.14 -m pytest tests/test_marketplace_plugins.py tests/test_wasm_runner.py tests/test_plugin_engine.py -v

# Full suite (some tests need optional deps: aiosqlite, pytest-asyncio for 3.14)
python3.14 -m pytest tests/ -v --ignore=tests/test_store.py --ignore=tests/integrated_test.py
```

## Known Constraints
- `tests/test_store.py` requires `aiosqlite` (not installed)
- `tests/integrated_test.py` requires `pytest-asyncio` compatible with Python 3.14
- `rotator.py` is a God Object (770+ lines, 27 routes) — splitting is in the backlog (J.6)
- PII detection uses regex (email/phone/SSN) — no ML/NER models

## Common Pitfalls
- **HTML index.html**: Views are nested divs with `hidden` class. Nesting bugs cause entire views to disappear — always verify tag balance.
- **Plugin timeout defaults**: 500ms for legacy functions, 50ms for BasePlugin classes.
- **AST scanner**: Blocks `os`, `subprocess`, `socket`, `exec`, `eval` in plugins — this is intentional security.
- **Font stack**: Terminal uses `'JetBrains Mono', 'Fira Code', monospace` (JetBrains Mono is primary).

## Style
- Backend: Python, type hints encouraged, async/await everywhere
- Frontend: Vanilla JS ES Modules, Tailwind CSS utility classes, glassmorphism dark theme
- Commits: descriptive, in English
- OGGI.md: session-based work log, in Italian
