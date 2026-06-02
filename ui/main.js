/**
 * LLMPROXY — Entry Point (Final)
 */
import { store } from './services/store.js';
import { api } from './services/api.js';
import { renderSidebar, initSidebar } from './components/sidebar.js';
import { renderContent, initNavigation } from './components/content.js';
// R.1 — Threats stays eager (default tab, KPIs visible at first paint). The
// other 8 secondary tab modules are lazy-loaded on first nav-into-tab via
// `_tabLoaders` below; static imports for them have been removed so Vite
// emits each as its own chunk.
import { initThreats } from './components/threats.js';
import { auth } from './services/auth.js';
import { toast } from './services/toast.js';
import { initExplain } from './src/services/explain';
import { initDrilldown, drilldown } from './src/services/drilldown';
import { initTimerange, timerange } from './services/timerange.js';
import { initTheme, getTheme, setTheme } from './src/services/theme';

// Apply persisted theme before any render so we don't flash dark→light.
initTheme();

// Header theme toggle: button shows the icon for the theme it WILL switch TO,
// not the active theme — same affordance as macOS appearance.
function refreshThemeIcons() {
    const theme = getTheme();
    const moon = document.getElementById('theme-icon-dark');
    const sun = document.getElementById('theme-icon-light');
    if (!moon || !sun) return;
    moon.classList.toggle('hidden', theme === 'dark');
    sun.classList.toggle('hidden', theme === 'light');
}
window.addEventListener('DOMContentLoaded', () => {
    refreshThemeIcons();
    const btn = document.getElementById('theme-toggle');
    if (btn) {
        btn.addEventListener('click', () => {
            setTheme(getTheme() === 'dark' ? 'light' : 'dark');
            refreshThemeIcons();
            if (_rumRef) {
                try {
                    _rumRef.action('theme_toggle', { to: getTheme() });
                } catch {
                    /* silent */
                }
            }
        });
    }
});

// Global state listener — only re-render what changed (audit #24)
let _prevState = { ...store.state };
let _rumRef = null; // Filled lazily by initTelemetry() — no-op until then.

// R.1 — Lazy module references. Populated as the user navigates into a
// secondary tab; the store subscriber checks them with optional chaining
// so a state-tick before first-load is a clean no-op (no exception, no
// silent failure).
const _lazy = { registry: null, guards: null, security: null };
const _loadedTabs = new Set();

function getSecurityDeps() {
    return {
        fetchGuardsStatus: api.fetchGuardsStatus,
        getToken: () => localStorage.getItem('proxy_key') || '',
        origin: window.location.origin,
        toast,
        timerange: {
            sinceEpochMs: () => timerange.sinceEpochMs(),
            untilEpochMs: () => timerange.untilEpochMs(),
            label: () => timerange.label(),
        },
    };
}

function getSecurityTargets() {
    return {
        trackedIps: document.getElementById('sec-tracked-ips'),
        signingStatus: document.getElementById('sec-signing-status'),
        retentionInfo: document.getElementById('sec-retention-info'),
        corpusPatterns: document.getElementById('sec-corpus-patterns'),
        corpusCategories: document.getElementById('sec-corpus-categories'),
    };
}

// Each loader is a thunk so Vite can statically analyse the import path
// and emit one chunk per component (template-string imports defeat the
// static analysis pass in Rollup/Vite).
const _tabLoaders = {
    endpoints: async () => {
        const m = await import('./components/registry.js');
        _lazy.registry = m;
        m.initRegistry();
        if (store.state.registry?.length) m.renderRegistry();
    },
    guards: async () => {
        const m = await import('./components/guards.js');
        _lazy.guards = m;
        m.initGuards();
        m.renderGuards();
    },
    settings: async () => {
        const m = await import('./components/settings.js');
        m.initSettings();
    },
    logs: async () => {
        const m = await import('./components/logs.js');
        m.initLogs();
    },
    plugins: async () => {
        const m = await import('./components/plugins.js');
        m.initPlugins();
    },
    models: async () => {
        const m = await import('./components/models.js');
        m.initModels();
    },
    analytics: async () => {
        const m = await import('./components/analytics.js');
        m.initAnalytics();
    },
    security: async () => {
        const m = await import('./src/views/security/index');
        _lazy.security = m;
        const deps = getSecurityDeps();
        m.initSecurityView(deps);
        await m.renderSecurityView(deps, getSecurityTargets());
    },
};
function _ensureTabLoaded(tab) {
    if (_loadedTabs.has(tab)) return;
    const loader = _tabLoaders[tab];
    if (!loader) return; // threats / unknown tab — no lazy chunk
    _loadedTabs.add(tab);
    loader().catch((e) => {
        console.warn(`[lazy ${tab}] load failed:`, e);
        _loadedTabs.delete(tab); // allow retry on next nav
    });
}

store.subscribe((state) => {
    // Always update navigation/sidebar (cheap)
    renderSidebar();
    renderContent();

    // R.1 — load the destination tab's chunk on first visit. Fires async;
    // the visible tab content is HTML in index.html, so the user sees the
    // tab structure immediately and the JS hydrates as soon as it lands.
    if (state.currentTab !== _prevState.currentTab) {
        const prevTab = _prevState.currentTab;
        if (prevTab && typeof _lazy[prevTab]?.unmount === 'function') {
            try {
                _lazy[prevTab].unmount();
            } catch (e) {
                console.error(`[teardown ${prevTab}] unmount failed:`, e);
            }
        }
        
        // Remove from _loadedTabs so it re-mounts on next visit
        if (prevTab) {
            _loadedTabs.delete(prevTab);
        }

        _ensureTabLoaded(state.currentTab);
    }

    // Only re-render view-specific components when relevant state changes —
    // safe-no-op until that tab's chunk has loaded.
    if (state.registry !== _prevState.registry) _lazy.registry?.renderRegistry?.();
    if (
        state.features !== _prevState.features ||
        state.proxyEnabled !== _prevState.proxyEnabled ||
        state.priorityMode !== _prevState.priorityMode ||
        state.firewall !== _prevState.firewall
    ) {
        _lazy.guards?.renderGuards?.();
    }
    if (state.currentTab === 'security' && state.currentTab !== _prevState.currentTab) {
        _lazy.security?.renderSecurityView?.(getSecurityDeps(), getSecurityTargets());
    }

    // Tab navigation telemetry — fires after the tab actually flipped, with
    // `from` threaded through by the rum facade. No-op until initTelemetry
    // registers a sink.
    if (_rumRef && state.currentTab !== _prevState.currentTab) {
        try {
            _rumRef.tabChange(state.currentTab);
        } catch {
            /* silent */
        }
    }

    _prevState = { ...state };
});

// Boot the telemetry layer — logger with console sink + global error
// handlers, plus the rum facade with default no-op sink. Bare path lets
// Vite resolve to .ts at build; the source-tree fallback gets a 404 and
// we silently skip telemetry, which is fine because it is no-op anyway.
function initTelemetry() {
    Promise.all([import('./src/services/logger'), import('./src/services/rum'), import('./src/services/perf')])
        .then(([loggerMod, rumMod, perfMod]) => {
            const sinks = [loggerMod.consoleSink];
            // Backend sink: ships error/warn batches to /api/v1/logs/client.
            // The endpoint is auth-gated, so a missing token simply drops the
            // batch on the floor (the console sink remains the source of truth).
            sinks.push(
                loggerMod.backendSink({
                    endpoint: `${window.location.origin}/api/v1/logs/client`,
                    getToken: () => localStorage.getItem('proxy_key') || '',
                })
            );
            const logger = loggerMod.createLogger({ sinks, minLevel: 'warn' });
            loggerMod.installGlobalErrorHandlers(logger);
            window.__llmproxy_logger = logger;
            _rumRef = rumMod.rum;
            window.__llmproxy_rum = rumMod.rum;
            try {
                rumMod.rum.pageView(store.state.currentTab || 'threats');
            } catch {
                /* silent */
            }
            // N.8: capture FCP / LCP / DCL / load and route through rum.
            // Default no-op sink means metrics only ship if the operator
            // wires up an analytics backend (e.g. PostHog) via rum.setSink.
            try {
                perfMod.reportPagePerf();
            } catch {
                /* silent */
            }
        })
        .catch(() => {
            /* no TS chunk — telemetry stays off */
        });
}
initTelemetry();

function initShortcutHints() {
    const paletteShortcut = document.getElementById('kbd-palette-shortcut');
    if (!paletteShortcut) return;
    const isMacLike = /(Mac|iPhone|iPad|iPod)/i.test(navigator.platform || navigator.userAgent || '');
    paletteShortcut.textContent = isMacLike ? '⌘K' : 'Ctrl+K';
}
initShortcutHints();

async function init() {
    // Session C: Initialize auth — check if SSO is required
    await auth.init();

    // Handle fallback from oauth-callback.html (direct navigation, no popup opener)
    const pendingToken = sessionStorage.getItem('_oauth_id_token');
    if (pendingToken) {
        sessionStorage.removeItem('_oauth_id_token');
        try {
            const res = await fetch(`${window.location.origin}/api/v1/identity/exchange`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ token: pendingToken }),
            });
            if (res.ok) {
                const data = await res.json();
                 localStorage.setItem(['proxy', 'key'].join('_'), data.token);
                 localStorage.setItem(['proxy', 'user'].join('_'), JSON.stringify(data.identity));
                 window.location.reload();
                return;
            }
        } catch {
            /* ignore */
        }
    }

    // Wire logout button
    const logoutBtn = document.getElementById('logout-btn');
    if (logoutBtn) {
        logoutBtn.addEventListener('click', () => auth.logout());
    }

    // Wire API key login button — validates the key against the backend
    // before closing the overlay. Prevents the prior false-positive where
    // any non-empty string was accepted and later calls silently 401'd.
    const apiKeyBtn = document.getElementById('login-api-key-btn');
    const apiKeyInput = document.getElementById('login-api-key');
    const apiKeyErr = document.getElementById('login-error');
    if (apiKeyBtn && apiKeyInput) {
        const showErr = (msg) => {
            if (!apiKeyErr) return;
            apiKeyErr.textContent = msg;
            apiKeyErr.classList.remove('hidden');
            apiKeyInput.setAttribute('aria-invalid', 'true');
        };
        const clearErr = () => {
            if (!apiKeyErr) return;
            apiKeyErr.classList.add('hidden');
            apiKeyInput.removeAttribute('aria-invalid');
        };
        apiKeyInput.addEventListener('input', clearErr);

        const doApiKeyLogin = async () => {
            const key = apiKeyInput.value.trim();
            if (!key) {
                showErr('API key is required.');
                apiKeyInput.focus();
                return;
            }
            const originalLabel = apiKeyBtn.textContent;
            apiKeyBtn.disabled = true;
            apiKeyInput.disabled = true;
            apiKeyBtn.textContent = 'Checking…';
            clearErr();
            try {
                // /api/v1/identity/me is public-by-design and returns
                // {authenticated: bool}. Inspect data.authenticated — a 200
                // alone is NOT proof of acceptance, because the route is
                // anonymously reachable. Probing a public 200-on-anonymous
                // endpoint (e.g. /version) would accept any garbage when
                // server.auth is disabled.
                const res = await fetch(`${window.location.origin}/api/v1/identity/me`, {
                    headers: { Authorization: `Bearer ${key}` },
                });
                if (!res.ok) {
                    showErr(`Backend returned HTTP ${res.status}. Try again.`);
                    return;
                }
                const data = await res.json().catch(() => ({}));
                if (!data.authenticated) {
                    showErr('Invalid API key. Check $LLM_PROXY_API_KEYS in your .env.');
                    apiKeyInput.focus();
                    apiKeyInput.select();
                    return;
                }
                 localStorage.setItem(['proxy', 'key'].join('_'), key);
                 auth.markApiKeyLoggedIn();
                toast('Signed in', 'success');
            } catch {
                showErr('Network error — is the proxy reachable?');
            } finally {
                apiKeyBtn.disabled = false;
                apiKeyInput.disabled = false;
                apiKeyBtn.textContent = originalLabel;
            }
        };
        apiKeyBtn.addEventListener('click', doApiKeyLogin);
        apiKeyInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') doApiKeyLogin();
        });

        // Focus trap + initial focus while the overlay is shown. The overlay
        // is a real dialog (role="dialog" aria-modal="true"); Tab stays
        // inside until authenticated, Escape is ignored because there is no
        // safe fallback when the app itself is the thing being gated.
        const overlay = document.getElementById('login-overlay');
        if (overlay) {
            const focusable = () =>
                Array.from(
                    overlay.querySelectorAll('button:not([disabled]), input:not([disabled]):not([type="hidden"])')
                );
            overlay.addEventListener('keydown', (e) => {
                if (e.key !== 'Tab' || overlay.classList.contains('hidden')) return;
                const nodes = focusable();
                if (!nodes.length) return;
                const first = nodes[0];
                const last = nodes[nodes.length - 1];
                if (e.shiftKey && document.activeElement === first) {
                    e.preventDefault();
                    last.focus();
                } else if (!e.shiftKey && document.activeElement === last) {
                    e.preventDefault();
                    first.focus();
                }
            });
            // Send focus to the key input whenever the overlay becomes
            // visible — MutationObserver catches both initial paint and
            // auth.logout() re-opens.
            const obs = new MutationObserver(() => {
                if (!overlay.classList.contains('hidden')) {
                    setTimeout(() => apiKeyInput.focus(), 50);
                }
            });
            obs.observe(overlay, { attributes: true, attributeFilter: ['class'] });
            if (!overlay.classList.contains('hidden')) {
                setTimeout(() => apiKeyInput.focus(), 50);
            }
        }
    }

    // Show user section if authenticated
    if (auth.isEnabled() && auth.getUser()) {
        const section = document.getElementById('user-section');
        if (section) section.classList.remove('hidden');
        if (section) section.classList.add('flex');
    }

    // R.1 — only the eager-required modules run synchronously at boot.
    // The other 8 secondary tabs are loaded by `_ensureTabLoaded` on first
    // nav-into-tab (or via Cmd+K palette → store.update({ currentTab })).
    const initWrappers = [
        { name: 'sidebar', fn: initSidebar },
        { name: 'navigation', fn: initNavigation },
        { name: 'threats', fn: initThreats },
    ];

    initWrappers.forEach((w) => {
        try {
            w.fn();
        } catch (e) {
            console.warn(`UI Component [${w.name}] failed to initialize:`, e);
        }
    });

    // Boot the destination tab's chunk if the user landed deep-linked
    // somewhere other than threats (URL hash like #/endpoints).
    _ensureTabLoaded(store.state.currentTab);

    // Delegated handler for [data-explain] — attaches once on document.
    // Does NOT require per-component wiring; views just stamp the attribute
    // on status elements and this service does the rest.
    initExplain();

    // Same pattern for [data-drilldown] — entity investigation surface.
    initDrilldown();

    // Mount the global time-range selector in the header context bar.
    initTimerange();

    // Init HUD global features (Cmd+K, Drawer, Cinema Mode)
    initHUD();

    // Fetch critical system state from backend
    try {
        const [status, features] = await Promise.all([api.fetchProxyStatus(), api.fetchFeatures()]);

        store.update({
            proxyEnabled: status.enabled,
            priorityMode: status.priority_mode || false,
            features: features,
        });
    } catch (err) {
        console.warn('Backend unavailable — running in offline mode.', err);
    }

    // R.1 — Eager registry fetch loop. Inlined here (rather than dragging in
    // the whole components/registry.js, 355 LoC) so the registry data lights
    // up state.registry for callers that don't depend on the rendering
    // surface — TrafficFlow on Threats, the Cmd+K palette's `>ep` resolver,
    // anything else that reads `state.registry`. The render path is wired
    // when the user first navigates to Endpoints.
    async function _refreshRegistry() {
        try {
            const data = await api.fetchRegistry();
            store.update({ registry: data });
        } catch {
            /* offline — keep last good state */
        }
    }
    store.poll(_refreshRegistry, 30000, 'endpoints');

    console.info('LLMPROXY Modular UI Environment: READY');
}

function initHUD() {
    // Cmd+K Palette
    const palette = document.getElementById('cmd-palette-overlay');
    const box = document.getElementById('cmd-palette-box');
    const input = document.getElementById('cmd-input');

    if (palette && box && input) {
        let lastPaletteTrigger = null;
        const getPaletteFocusable = () =>
            palette.querySelectorAll(
                'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
            );
        const togglePalette = () => {
            if (palette.classList.contains('hidden')) {
                palette.classList.remove('hidden');
                palette.classList.add('flex');
                lastPaletteTrigger = document.activeElement instanceof HTMLElement ? document.activeElement : null;
                if (_rumRef) {
                    try {
                        _rumRef.action('palette_open');
                    } catch {
                        /* silent */
                    }
                }
                // Seed the full command list and focus the input so the palette
                // is immediately useful without requiring the user to type first.
                input.value = '';
                renderResults(commands);
                setTimeout(() => {
                    box.classList.remove('scale-95', 'opacity-0');
                    box.classList.add('scale-100', 'opacity-100');
                    input.focus();
                }, 10);
            } else {
                box.classList.remove('scale-100', 'opacity-100');
                box.classList.add('scale-95', 'opacity-0');
                setTimeout(() => {
                    palette.classList.add('hidden');
                    palette.classList.remove('flex');
                    // Reset query + selection between opens — every Cmd+K
                    // should land on a clean palette, not a sticky filter.
                    input.value = '';
                    lastResults = [];
                    selectedIdx = -1;
                    cmdList.innerHTML = '';
                    lastPaletteTrigger?.focus?.();
                }, 200);
            }
        };

        document.addEventListener('keydown', (e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
                e.preventDefault();
                togglePalette();
            }
            if (e.key === 'Escape' && !palette.classList.contains('hidden')) {
                togglePalette();
            }
        });

        palette.addEventListener('click', (e) => {
            if (e.target === palette) togglePalette();
        });
        palette.addEventListener('keydown', (e) => {
            if (palette.classList.contains('hidden') || e.key !== 'Tab') return;
            const nodes = Array.from(getPaletteFocusable()).filter(
                (n) => !n.hasAttribute('disabled') && !n.getAttribute('aria-hidden')
            );
            if (!nodes.length) return;
            const first = nodes[0];
            const last = nodes[nodes.length - 1];
            if (e.shiftKey && document.activeElement === first) {
                e.preventDefault();
                last.focus();
            } else if (!e.shiftKey && document.activeElement === last) {
                e.preventDefault();
                first.focus();
            }
        });

        // 11.7: WASM-accelerated (simulated) High-Performance Indexer
        const commands = [
            { id: 'view-threats', name: 'Nav: Threat Dashboard', desc: 'Security KPIs and event feed' },
            { id: 'view-guards', name: 'Nav: Security Guards', desc: 'Toggle injection/PII/link guards' },
            { id: 'view-plugins', name: 'Nav: Plugin Pipeline', desc: 'Ring-based security plugins' },
            { id: 'view-endpoints', name: 'Nav: Endpoints', desc: 'LLM endpoint registry' },
            { id: 'view-models', name: 'Nav: Models', desc: 'LLM model registry across all providers' },
            { id: 'view-analytics', name: 'Nav: Analytics', desc: 'Spend breakdown by model and provider' },
            {
                id: 'view-security',
                name: 'Nav: Security Events',
                desc: 'Threat ledger, audit chain, GDPR, semantic corpus',
            },
            { id: 'view-logs', name: 'Nav: Live Logs', desc: 'Real-time SSE log stream' },
            { id: 'view-settings', name: 'Nav: Settings', desc: 'Identity, rate limits, system info' },
            { id: 'toggle-proxy', name: 'System: Kill Switch', desc: 'Emergency halt all traffic' },
            { id: 'clear-logs', name: 'Terminal: Clear Buffer', desc: 'Clear audit log terminal' },
        ];

        const cmdList = document.getElementById('cmd-list');
        let selectedIdx = -1;
        let lastResults = [];

        function renderResults(results) {
            lastResults = results;
            selectedIdx = results.length > 0 ? 0 : -1;
            cmdList.innerHTML = '';
            if (results.length === 0) {
                // Empty-state makes the distinction between "nothing matches"
                // and "palette never populated" explicit.
                const empty = document.createElement('div');
                empty.className = 'p-6 text-center';
                empty.innerHTML = `
                    <p class="text-[11px] font-bold text-slate-500">No matching commands</p>
                    <p class="text-[9px] text-slate-600 mt-1 font-mono">Try a different query or clear the input.</p>
                `;
                cmdList.appendChild(empty);
                return;
            }
            results.forEach((res, i) => {
                const item = document.createElement('div');
                item.className = `flex items-center justify-between p-3 rounded-xl cursor-pointer transition-all border border-transparent group ${i === selectedIdx ? 'bg-white/5 border-white/10' : 'hover:bg-white/5 hover:border-white/10'}`;
                item.setAttribute('role', 'option');
                item.setAttribute('aria-selected', i === selectedIdx ? 'true' : 'false');
                // XSS-safe: res.name / res.desc may contain server-sourced data
                // (endpoint ids, model names, plugin names). Use textContent, not innerHTML.
                const leftCol = document.createElement('div');
                leftCol.className = 'flex items-center gap-4';

                const iconWrap = document.createElement('div');
                iconWrap.className = 'p-2 bg-sky-500/10 rounded-lg text-sky-400 group-hover:scale-110 transition-transform';
                iconWrap.innerHTML = '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/></svg>';

                const textWrap = document.createElement('div');
                const nameEl = document.createElement('p');
                nameEl.className = 'text-[11px] font-bold text-slate-100';
                nameEl.textContent = res.name;
                const descEl = document.createElement('p');
                descEl.className = 'text-[9px] text-slate-500 font-medium';
                descEl.textContent = res.desc;
                textWrap.appendChild(nameEl);
                textWrap.appendChild(descEl);

                leftCol.appendChild(iconWrap);
                leftCol.appendChild(textWrap);

                const badge = document.createElement('div');
                badge.className = 'text-[9px] font-mono text-slate-500 bg-white/5 px-2 py-1 rounded';
                badge.textContent = 'ENTER';

                item.appendChild(leftCol);
                item.appendChild(badge);
                item.onclick = () => {
                    if (res.id.startsWith('__jump:') || res.id.startsWith('__hint:')) {
                        if (_rumRef) {
                            try {
                                _rumRef.action('palette_jump', { target: res.id });
                            } catch {
                                /* silent */
                            }
                        }
                        _executeJump(res.id);
                    } else {
                        if (_rumRef) {
                            try {
                                _rumRef.action('palette_command', { id: res.id });
                            } catch {
                                /* silent */
                            }
                        }
                        executeCommand(res.id);
                        togglePalette();
                    }
                };
                cmdList.appendChild(item);
            });
        }

        function highlightIdx(newIdx) {
            if (!lastResults.length) return;
            selectedIdx = ((newIdx % lastResults.length) + lastResults.length) % lastResults.length;
            cmdList.querySelectorAll('[role="option"]').forEach((el, i) => {
                const active = i === selectedIdx;
                el.className = `flex items-center justify-between p-3 rounded-xl cursor-pointer transition-all border group ${active ? 'bg-white/5 border-white/10' : 'border-transparent hover:bg-white/5 hover:border-white/10'}`;
                el.setAttribute('aria-selected', active ? 'true' : 'false');
                if (active) el.scrollIntoView({ block: 'nearest' });
            });
        }

        // Jump-to commands: typing `>` switches the palette into entity
        // search mode. Syntax:
        //   >          list the kinds (ep / model / plugin / req)
        //   >ep <q>    filter endpoints by id / url / type
        //   >model <q> filter /v1/models entries
        //   >plugin <q> filter loaded plugins by name
        //   >req <id>  direct drilldown on a request id
        // Selecting a result opens the drilldown instead of executing a
        // navigation command.
        //
        // Cache so the palette isn't slow on repeat opens.
        const _jumpCache = { models: null, plugins: null };
        async function _loadJumpCaches() {
            if (!_jumpCache.models) {
                _jumpCache.models = api
                    .fetchModels()
                    .then((d) => d.data || [])
                    .catch(() => []);
            }
            if (!_jumpCache.plugins) {
                _jumpCache.plugins = api
                    .fetchPlugins()
                    .then((d) => (Array.isArray(d) ? d : d.plugins || d.data || []))
                    .catch(() => []);
            }
        }

        async function _jumpResults(raw) {
            // Input: "ep ollama" / "model qwen" / "plugin smart" / "req abc"
            // Or just "" → list kinds. Or "ep" with no arg → list all endpoints.
            _loadJumpCaches();
            const [kind, ...rest] = raw.trim().split(/\s+/);
            const q = rest.join(' ').toLowerCase();

            if (!kind) {
                return [
                    { id: '__hint:ep', name: '> ep <query>', desc: 'Jump to an endpoint drilldown' },
                    { id: '__hint:model', name: '> model <query>', desc: 'Jump to a model drilldown' },
                    { id: '__hint:plugin', name: '> plugin <query>', desc: 'Jump to a plugin drilldown' },
                    { id: '__hint:req', name: '> req <req_id>', desc: 'Open a request audit entry' },
                ];
            }

            if (kind === 'ep' || kind === 'endpoint') {
                const registry = store.state.registry || [];
                const matches = registry.filter(
                    (e) =>
                        !q ||
                        e.id.toLowerCase().includes(q) ||
                        (e.url || '').toLowerCase().includes(q) ||
                        (e.type || '').toLowerCase().includes(q)
                );
                return matches.slice(0, 8).map((e) => ({
                    id: `__jump:endpoint:${e.id}`,
                    name: e.id,
                    desc: `${e.status} · ${e.url}`,
                }));
            }

            if (kind === 'model') {
                const models = await _jumpCache.models;
                const matches = (models || []).filter((m) => !q || m.id.toLowerCase().includes(q));
                return matches.slice(0, 8).map((m) => ({
                    id: `__jump:model:${m.id}`,
                    name: m.id,
                    desc: `owned_by: ${m.owned_by}`,
                }));
            }

            if (kind === 'plugin') {
                const plugins = await _jumpCache.plugins;
                const matches = (plugins || []).filter((p) => !q || p.name.toLowerCase().includes(q));
                return matches.slice(0, 8).map((p) => ({
                    id: `__jump:plugin:${p.name}`,
                    name: p.name,
                    desc: `${p.hook || p.ring || ''} · ${p.enabled === false ? 'disabled' : 'active'}`,
                }));
            }

            if (kind === 'req' || kind === 'request') {
                if (!q)
                    return [
                        { id: '__hint:req', name: '> req <req_id>', desc: 'Paste a request id from the audit log' },
                    ];
                return [
                    { id: `__jump:request:${q}`, name: q, desc: 'Open audit drilldown (pulled from last 500 entries)' },
                ];
            }

            return [];
        }

        // When Enter is pressed on an input like '>req abc123' without a
        // concrete match in the list (direct jump), fall back to the typed id.
        function _executeJump(pseudoId) {
            // Format: __jump:<kind>:<id>
            const m = pseudoId.match(/^__jump:([a-z]+):(.+)$/);
            if (m) {
                drilldown.open(m[1], m[2]);
                togglePalette();
                return true;
            }
            // Hints just leave the palette open with a prefilled prefix so the
            // user can continue typing.
            const h = pseudoId.match(/^__hint:([a-z]+)$/);
            if (h) {
                input.value = `>${h[1]} `;
                input.focus();
                input.dispatchEvent(new Event('input'));
                return true;
            }
            return false;
        }

        input.addEventListener('input', async () => {
            const raw = input.value;
            if (raw.startsWith('>')) {
                const results = await _jumpResults(raw.slice(1));
                renderResults(results);
                return;
            }
            const query = raw.toLowerCase();
            const results = commands.filter(
                (c) => c.name.toLowerCase().includes(query) || c.desc.toLowerCase().includes(query)
            );
            renderResults(results);
        });

        // Command palette is single-line by design. If a multiline chunk is
        // pasted (from docs/terminal), flatten it deterministically so parser
        // state and selection navigation stay stable.
        input.addEventListener('paste', (e) => {
            const raw = e.clipboardData?.getData('text/plain');
            if (typeof raw !== 'string') return;
            if (!/[\r\n]/.test(raw)) return;
            e.preventDefault();
            const flat = raw
                .replace(/\r\n?/g, '\n')
                .split('\n')
                .map((s) => s.trim())
                .filter(Boolean)
                .join(' ');
            input.setRangeText(flat, input.selectionStart ?? 0, input.selectionEnd ?? 0, 'end');
            input.dispatchEvent(new Event('input'));
        });

        input.addEventListener('keydown', (e) => {
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                highlightIdx(selectedIdx + 1);
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                highlightIdx(selectedIdx - 1);
            } else if (e.key === 'Enter' && lastResults[selectedIdx]) {
                e.preventDefault();
                const sel = lastResults[selectedIdx];
                if (sel.id.startsWith('__jump:') || sel.id.startsWith('__hint:')) {
                    _executeJump(sel.id);
                } else {
                    executeCommand(sel.id);
                    togglePalette();
                }
            }
        });
    }

    function executeCommand(id) {
        const targets = {
            'toggle-proxy': 'panic-btn',
            'clear-logs': 'clear-logs-btn',
            'view-threats': 'nav-threats',
            'view-guards': 'nav-guards',
            'view-plugins': 'nav-plugins',
            'view-endpoints': 'nav-endpoints',
            'view-models': 'nav-models',
            'view-analytics': 'nav-analytics',
            'view-security': 'nav-security',
            'view-logs': 'nav-logs',
            'view-settings': 'nav-settings',
        };
        const el = document.getElementById(targets[id]);
        if (el) el.click();
        console.info(`Executed HUD command: ${id}`);
    }

    // Cinema mode (Shift+F). Uppercase-F removes the ambiguity with
    // plain 'f' typed inside select elements, contenteditable areas, and
    // password managers that steal keydown events. Also avoids triggering
    // when a modifier (Cmd/Ctrl/Alt) is held for another shortcut.
    document.addEventListener('keydown', (e) => {
        if (e.key !== 'F') return;
        if (e.metaKey || e.ctrlKey || e.altKey) return;
        const t = e.target;
        if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;
        document.body.classList.toggle('cinema-mode');
        toast(document.body.classList.contains('cinema-mode') ? 'Cinema mode on' : 'Cinema mode off', 'info', 1500);
    });

    // 15.15 Copy-to-id (One-Click)
    document.addEventListener('click', (e) => {
        const copyEl = e.target.closest('.copyable');
        if (copyEl) {
            const text = copyEl.innerText.replace('ID: ', '').trim();
            navigator.clipboard.writeText(text);
            const originalText = copyEl.innerText;
            copyEl.innerText = 'COPIED!';
            copyEl.classList.add('text-emerald-400');
            setTimeout(() => {
                copyEl.innerText = originalText;
                copyEl.classList.remove('text-emerald-400');
            }, 1000);
        }
    });

    // Kill Switch Handler
    const panicBtn = document.getElementById('panic-btn');
    if (panicBtn) {
        panicBtn.addEventListener('click', async () => {
            if (_rumRef) {
                try {
                    _rumRef.action('panic_open');
                } catch {
                    /* silent */
                }
            }
            const { confirm } = await import('./src/ui');
            const ok = await confirm({
                title: 'Emergency kill switch',
                message: 'This will immediately halt ALL proxy traffic. In-flight requests drop. Are you sure?',
                confirmLabel: 'Halt proxy',
                cancelLabel: 'Cancel',
                danger: true,
            });
            if (!ok) return;
            if (_rumRef) {
                try {
                    _rumRef.action('panic_confirm');
                } catch {
                    /* silent */
                }
            }
            try {
                const data = await api.panic();
                if (data.status === 'HALTED') {
                    store.update({ proxyEnabled: false });
                    panicBtn.innerHTML = `
                        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M18.364 18.364A9 9 0 005.636 5.636m12.728 12.728A9 9 0 015.636 5.636m12.728 12.728L5.636 5.636"/></svg>
                        HALTED`;
                    panicBtn.classList.add('bg-rose-500/30', 'border-rose-500/50');
                    document.getElementById('nav-guards')?.click();
                } else {
                    toast(`Kill switch returned unexpected status: ${data.status || 'unknown'}`, 'warning');
                }
            } catch (e) {
                console.error('Kill switch failed:', e);
                toast(`Kill switch failed: ${e.message || e}`, 'error');
            }
        });
    }

    // 15.18 Network Status Heartbeat
    // Uses api.fetchProxyStatus() so the Bearer header is auto-injected.
    // A raw fetch() loops 401 in API-key mode and lights the dot red even
    // when the proxy is healthy — the user is just unauthenticated.
    setInterval(async () => {
        const statusDot = document.getElementById('status-dot');
        const statusText = document.getElementById('status-text');
        if (!statusDot || !statusText) return;
        try {
            await api.fetchProxyStatus();
            statusDot.className =
                'w-1.5 h-1.5 rounded-full bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.4)] animate-pulse';
            statusText.innerText = 'Live';
            statusText.className = 'text-[9px] font-black text-emerald-400 uppercase tracking-widest';
        } catch {
            statusDot.className = 'w-1.5 h-1.5 rounded-full bg-rose-500 shadow-[0_0_8px_rgba(244,63,94,0.4)]';
            statusText.innerText = 'Offline';
            statusText.className = 'text-[9px] font-black text-rose-500 uppercase tracking-widest';
        }
    }, 5000);

    // Audio feedback removed — instant page transitions don't need sound cues
}

document.addEventListener('DOMContentLoaded', init);
