/**
 * Universal entity drilldown.
 *
 * Coherent investigation surface across the 4 core entities. Every
 * drilldown follows the same tab grammar so the operator never has to
 * relearn the UI when jumping between views:
 *
 *   overview | timeline | config | related | actions
 *
 * Supported kinds (MVP):
 *   endpoint  — a configured or discovered LLM endpoint
 *   request   — a single audit row (req_id)
 *
 * Future kinds (scope deferred):
 *   model     — providers that advertise a given model id
 *   plugin    — hot-swap history + per-plugin stats
 */

import { api } from './api.js';
import { store } from './store.js';
import { toast } from './toast.js';

const BASE_URL = window.location.origin;

function _authFetch(path, opts = {}) {
    const token = localStorage.getItem('proxy_key') || '';
    const headers = { ...(opts.headers || {}) };
    if (token) headers['Authorization'] = `Bearer ${token}`;
    return fetch(`${BASE_URL}${path}`, { ...opts, headers }).then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
    });
}

function _fmtTs(ts) {
    if (!ts) return '—';
    const n = typeof ts === 'number' ? ts * (ts < 1e12 ? 1000 : 1) : Date.parse(ts);
    if (Number.isNaN(n)) return String(ts);
    return new Date(n).toLocaleString();
}

function _kv(label, value) {
    const v = value == null || value === '' ? '—' : String(value);
    return `
        <div class="grid grid-cols-[110px_1fr] gap-2 py-1.5 border-b border-white/[0.04] last:border-0">
            <span class="text-[10px] font-bold text-slate-500 uppercase tracking-wide">${label}</span>
            <span class="text-[11px] text-white font-mono break-all">${v}</span>
        </div>`;
}

// ── Tab scaffold ───────────────────────────────────────────────────────────

function _tabBar(tabs, active, onPick) {
    const wrap = document.createElement('div');
    wrap.className = 'flex items-center gap-1 border-b border-white/[0.06] -mx-5 px-5 mb-4 sticky top-[48px] bg-[#0a0a0c]/95 backdrop-blur';
    for (const t of tabs) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.textContent = t;
        const isActive = t === active;
        btn.className = `px-3 py-2 text-[10px] font-bold uppercase tracking-wide transition-colors border-b-2 ${isActive ? 'text-white border-cyan-500' : 'text-slate-500 hover:text-white border-transparent'}`;
        btn.addEventListener('click', () => onPick(t));
        wrap.appendChild(btn);
    }
    return wrap;
}

function _loading() {
    const el = document.createElement('div');
    el.className = 'py-8 text-center text-[11px] text-slate-500 font-mono';
    el.textContent = 'Loading…';
    return el;
}

function _errorBody(msg) {
    const el = document.createElement('div');
    el.className = 'py-6 text-[11px] text-rose-400 font-mono';
    el.textContent = msg;
    return el;
}

// ── Entity builders ────────────────────────────────────────────────────────

async function _endpointTabs(id) {
    // Snapshot current registry + circuit/stats — already in store from threats view.
    const registry = store.state.registry || [];
    const ep = registry.find(e => e.id === id);
    if (!ep) throw new Error(`Endpoint '${id}' not found`);

    // Related audit rows where this endpoint was picked (using 'provider' column).
    let relatedRows = [];
    try {
        // Coarse filter — audit has no endpoint param, so we pull the top N and
        // filter client-side by provider name that matches the endpoint's type.
        const recent = await _authFetch(`/api/v1/audit?limit=50`);
        const pool = recent.items || [];
        relatedRows = pool.filter(r =>
            r.provider === ep.type || r.provider === ep.id || (ep.id.startsWith(r.provider + '-'))
        ).slice(0, 12);
    } catch { /* audit optional */ }

    return {
        overview: () => _endpointOverview(ep),
        timeline: () => _endpointTimeline(relatedRows),
        config: () => _endpointConfig(ep),
        related: () => _endpointRelated(ep, registry),
        actions: () => _endpointActions(ep),
    };
}

function _endpointOverview(ep) {
    const el = document.createElement('div');
    el.innerHTML = `
        ${_kv('ID', ep.id)}
        ${_kv('URL', ep.url)}
        ${_kv('Type', ep.type || 'Generic')}
        ${_kv('Status', ep.status)}
        ${_kv('Circuit', `${(ep.circuit_state || 'closed').toUpperCase()} (${ep.failure_count || 0}/${ep.failure_threshold || 5})`)}
        ${_kv('Latency', ep.latency || '—')}
        ${_kv('Priority', ep.priority)}
    `;
    return el;
}

function _endpointTimeline(rows) {
    const el = document.createElement('div');
    if (!rows.length) {
        el.innerHTML = '<p class="text-[11px] text-slate-600 font-mono">No recent audit entries matched this endpoint.</p>';
        return el;
    }
    el.innerHTML = rows.map(r => {
        const ts = _fmtTs(r.ts);
        const ok = !r.blocked && (r.status || 0) < 400;
        const badge = ok
            ? `<span class="text-emerald-400 text-[10px] font-mono">${r.status || '—'}</span>`
            : `<span class="text-rose-400 text-[10px] font-mono">${r.blocked ? 'BLOCK' : (r.status || 'ERR')}</span>`;
        return `
            <div class="py-2 border-b border-white/[0.04] last:border-0 cursor-pointer hover:bg-white/[0.02] -mx-2 px-2 rounded"
                 data-drilldown="request:${r.req_id}">
                <div class="flex items-center justify-between mb-0.5">
                    <span class="text-[9px] font-mono text-slate-500">${ts}</span>
                    ${badge}
                </div>
                <div class="flex items-center justify-between gap-2">
                    <span class="text-[11px] font-mono text-white truncate">${r.model || '—'}</span>
                    <span class="text-[10px] font-mono text-slate-500">${(r.latency_ms || 0).toFixed(0)}ms</span>
                </div>
            </div>`;
    }).join('');
    return el;
}

function _endpointConfig(ep) {
    const el = document.createElement('div');
    el.innerHTML = `
        <p class="text-[11px] text-slate-400 mb-3">
            Endpoints are defined in one of four places: config.yaml, .env (LLM_PROXY_ENDPOINT_*),
            the UI Add form, or runtime auto-discovery. The registry reflects whichever path
            registered this entry.
        </p>
        ${_kv('Source', ep._source || 'config')}
        ${_kv('Base URL', ep.url)}
        ${_kv('Type', ep.type || 'Generic')}
    `;
    return el;
}

function _endpointRelated(ep, registry) {
    const siblings = registry.filter(e => e.id !== ep.id && e.type === ep.type).slice(0, 8);
    const el = document.createElement('div');
    if (!siblings.length) {
        el.innerHTML = '<p class="text-[11px] text-slate-600 font-mono">No other endpoints of the same type.</p>';
        return el;
    }
    el.innerHTML = `
        <p class="text-[11px] text-slate-400 mb-3">Other endpoints routing the same provider type.</p>
        ${siblings.map(s => `
            <div class="py-2 border-b border-white/[0.04] last:border-0 cursor-pointer hover:bg-white/[0.02] -mx-2 px-2 rounded"
                 data-drilldown="endpoint:${s.id}">
                <div class="flex items-center justify-between">
                    <span class="text-[11px] font-mono text-white">${s.id}</span>
                    <span class="text-[9px] font-mono ${s.status === 'Live' ? 'text-emerald-400' : 'text-slate-500'}">${s.status}</span>
                </div>
                <p class="text-[9px] font-mono text-slate-500 truncate">${s.url}</p>
            </div>`).join('')}
    `;
    return el;
}

function _endpointActions(ep) {
    const el = document.createElement('div');
    el.innerHTML = `<div class="flex flex-col gap-2"></div>`;
    const wrap = el.firstElementChild;

    const btn = (label, tone, handler) => {
        const b = document.createElement('button');
        b.type = 'button';
        b.textContent = label;
        b.className = `px-3 py-2 rounded-lg text-[11px] font-bold transition-colors border ${
            tone === 'danger' ? 'bg-rose-500/10 hover:bg-rose-500/20 text-rose-300 border-rose-500/30'
            : tone === 'warn' ? 'bg-amber-500/10 hover:bg-amber-500/20 text-amber-300 border-amber-500/30'
            : 'bg-white/5 hover:bg-white/10 text-slate-300 border-white/10'
        }`;
        b.addEventListener('click', handler);
        return b;
    };

    wrap.appendChild(btn('Reset circuit breaker', 'warn', async () => {
        try {
            await api.resetCircuitBreaker(ep.id);
            toast(`Circuit breaker ${ep.id} reset to CLOSED`, 'success');
        } catch (e) {
            toast(`Reset failed: ${e.message}`, 'error');
        }
    }));

    wrap.appendChild(btn('Toggle enabled/disabled', 'warn', async () => {
        try {
            await api.toggleEndpoint(ep.id);
            toast(`Endpoint ${ep.id} toggled`, 'success');
        } catch (e) {
            toast(`Toggle failed: ${e.message}`, 'error');
        }
    }));

    wrap.appendChild(btn('Delete endpoint', 'danger', async () => {
        const { confirm } = await import('../src/ui');
        const ok = await confirm({
            title: 'Delete endpoint',
            message: `Remove "${ep.id}" from the registry? Active traffic will fall back via the fallback chain.`,
            confirmLabel: 'Delete',
            danger: true,
        });
        if (!ok) return;
        try {
            await api.deleteEndpoint(ep.id);
            toast(`Endpoint ${ep.id} deleted`, 'success');
        } catch (e) {
            toast(`Delete failed: ${e.message}`, 'error');
        }
    }));

    return el;
}

async function _requestTabs(reqId) {
    // Audit API doesn't support id lookup directly — scan the last window.
    let row = null;
    let related = [];
    try {
        const data = await _authFetch(`/api/v1/audit?limit=500`);
        const items = data.items || [];
        row = items.find(r => r.req_id === reqId) || null;
        if (row) {
            related = items.filter(r => r.session_id === row.session_id && r.req_id !== reqId).slice(0, 10);
        }
    } catch { /* handled below */ }

    if (!row) throw new Error(`Request '${reqId}' not found in recent audit window.`);

    return {
        overview: () => _requestOverview(row),
        timeline: () => _requestTimeline(related, reqId),
        config: () => _requestConfig(row),
        related: () => _requestRelated(related),
        actions: () => _requestActions(row),
    };
}

function _requestOverview(r) {
    const el = document.createElement('div');
    const ok = !r.blocked && (r.status || 0) < 400;
    el.innerHTML = `
        <div class="mb-4">
            <span class="text-2xl font-black ${ok ? 'text-emerald-400' : 'text-rose-400'}">${r.blocked ? 'BLOCKED' : (r.status || '—')}</span>
            <span class="text-[11px] text-slate-500 ml-2">${_fmtTs(r.ts)}</span>
        </div>
        ${_kv('Request ID', r.req_id)}
        ${_kv('Session', r.session_id)}
        ${_kv('Model', r.model)}
        ${_kv('Provider', r.provider)}
        ${_kv('Tokens', `${r.prompt_tokens || 0} prompt + ${r.completion_tokens || 0} completion`)}
        ${_kv('Cost', r.cost_usd ? `$${r.cost_usd.toFixed(6)}` : '—')}
        ${_kv('Latency', `${(r.latency_ms || 0).toFixed(1)} ms`)}
        ${_kv('Blocked', r.blocked ? `YES — ${r.block_reason || 'unknown'}` : 'no')}
        ${_kv('Key prefix', r.key_prefix || '—')}
    `;
    return el;
}

function _requestTimeline(related, _reqId) {
    const el = document.createElement('div');
    if (!related.length) {
        el.innerHTML = '<p class="text-[11px] text-slate-600 font-mono">No other requests in this session window.</p>';
        return el;
    }
    el.innerHTML = `<p class="text-[11px] text-slate-400 mb-3">Other requests from the same session — chronological.</p>` +
        related.map(r => {
            const ok = !r.blocked && (r.status || 0) < 400;
            return `
                <div class="py-2 border-b border-white/[0.04] last:border-0 cursor-pointer hover:bg-white/[0.02] -mx-2 px-2 rounded"
                     data-drilldown="request:${r.req_id}">
                    <div class="flex items-center justify-between mb-0.5">
                        <span class="text-[9px] font-mono text-slate-500">${_fmtTs(r.ts)}</span>
                        <span class="text-[10px] font-mono ${ok ? 'text-emerald-400' : 'text-rose-400'}">${r.blocked ? 'BLOCK' : (r.status || '—')}</span>
                    </div>
                    <div class="flex items-center justify-between gap-2">
                        <span class="text-[11px] font-mono text-white truncate">${r.model || '—'}</span>
                        <span class="text-[10px] font-mono text-slate-500">${(r.latency_ms || 0).toFixed(0)}ms</span>
                    </div>
                </div>`;
        }).join('');
    return el;
}

function _requestConfig(r) {
    const el = document.createElement('div');
    try {
        const meta = r.metadata ? JSON.parse(r.metadata) : {};
        const rows = Object.entries(meta).map(([k, v]) => _kv(k, typeof v === 'object' ? JSON.stringify(v) : v)).join('');
        el.innerHTML = rows || '<p class="text-[11px] text-slate-600 font-mono">No metadata recorded.</p>';
    } catch {
        el.innerHTML = `<pre class="text-[10px] text-slate-400 font-mono whitespace-pre-wrap">${r.metadata || '—'}</pre>`;
    }
    return el;
}

function _requestRelated(related) {
    const el = document.createElement('div');
    if (!related.length) {
        el.innerHTML = '<p class="text-[11px] text-slate-600 font-mono">No related entries.</p>';
        return el;
    }
    el.innerHTML = related.map(r => `
        <div class="py-2 border-b border-white/[0.04] last:border-0 cursor-pointer hover:bg-white/[0.02] -mx-2 px-2 rounded"
             data-drilldown="request:${r.req_id}">
            <span class="text-[10px] font-mono text-slate-400">${r.req_id}</span>
            <p class="text-[11px] font-mono text-white">${r.model || '—'}</p>
        </div>`).join('');
    return el;
}

function _requestActions(r) {
    const el = document.createElement('div');
    el.innerHTML = `
        <p class="text-[11px] text-slate-400 leading-relaxed mb-3">
            Audit entries are immutable (SHA-256 hash-chained). Use the Security view to export
            the full session or trigger a GDPR erase for this subject.
        </p>
        ${_kv('Entry hash', (r.entry_hash || '').slice(0, 32) + '…')}
        ${_kv('Prev hash', (r.prev_hash || '').slice(0, 32) + '…')}
    `;
    return el;
}

// ── Model kind ─────────────────────────────────────────────────────────────

async function _modelTabs(modelId) {
    // Which endpoints advertise this model?
    const registry = store.state.registry || [];
    const allModels = await api.fetchModels().catch(() => ({ data: [] }));
    const seen = (allModels.data || []).filter(m => m.id === modelId);
    if (!seen.length) throw new Error(`Model '${modelId}' not in /v1/models. Is any endpoint advertising it?`);

    // Providers reported via owned_by — can repeat (same model on multiple endpoints).
    const providers = [...new Set(seen.map(m => m.owned_by))];

    // Endpoints from registry whose type matches one of the providers OR
    // whose id contains the provider name (handles auto-discovery tags like
    // 'lmstudio-100-98-112-23').
    const candidates = registry.filter(ep =>
        providers.includes(ep.type) ||
        providers.some(p => ep.id === p || ep.id.startsWith(p + '-'))
    );

    // Recent audit slice for this model.
    let auditRows = [];
    try {
        const data = await _authFetch(`/api/v1/audit?model=${encodeURIComponent(modelId)}&limit=100`);
        auditRows = data.items || [];
    } catch { /* audit optional */ }

    return {
        overview: () => _modelOverview(modelId, providers, candidates, auditRows),
        timeline: () => _modelTimeline(auditRows),
        config: () => _modelConfig(modelId, providers),
        related: () => _modelRelated(candidates),
        actions: () => _modelActions(modelId),
    };
}

function _modelOverview(modelId, providers, candidates, auditRows) {
    const successes = auditRows.filter(r => !r.blocked && (r.status || 0) < 400).length;
    const blocked = auditRows.filter(r => r.blocked).length;
    const errors = auditRows.filter(r => !r.blocked && (r.status || 0) >= 400).length;
    const avgLatency = auditRows.length
        ? (auditRows.reduce((s, r) => s + (r.latency_ms || 0), 0) / auditRows.length).toFixed(0)
        : '—';
    const totalCost = auditRows.reduce((s, r) => s + (r.cost_usd || 0), 0);

    const el = document.createElement('div');
    el.innerHTML = `
        ${_kv('Model', modelId)}
        ${_kv('Providers', providers.join(', ') || '—')}
        ${_kv('Routable via', `${candidates.length} endpoint${candidates.length === 1 ? '' : 's'}`)}
        ${_kv('Recent requests', `${auditRows.length} (${successes} ok · ${errors} err · ${blocked} blocked)`)}
        ${_kv('Avg latency', avgLatency === '—' ? '—' : `${avgLatency} ms`)}
        ${_kv('Recent cost', `$${totalCost.toFixed(6)}`)}
    `;
    return el;
}

function _modelTimeline(rows) {
    const el = document.createElement('div');
    if (!rows.length) {
        el.innerHTML = '<p class="text-[11px] text-slate-600 font-mono">No audit entries for this model.</p>';
        return el;
    }
    el.innerHTML = rows.slice(0, 30).map(r => {
        const ok = !r.blocked && (r.status || 0) < 400;
        return `
            <div class="py-2 border-b border-white/[0.04] last:border-0 cursor-pointer hover:bg-white/[0.02] -mx-2 px-2 rounded"
                 data-drilldown="request:${r.req_id}">
                <div class="flex items-center justify-between mb-0.5">
                    <span class="text-[9px] font-mono text-slate-500">${_fmtTs(r.ts)}</span>
                    <span class="text-[10px] font-mono ${ok ? 'text-emerald-400' : 'text-rose-400'}">${r.blocked ? 'BLOCK' : (r.status || '—')}</span>
                </div>
                <div class="flex items-center justify-between">
                    <span class="text-[11px] font-mono text-slate-300">${r.provider || '—'}</span>
                    <span class="text-[10px] font-mono text-slate-500">${(r.latency_ms || 0).toFixed(0)}ms</span>
                </div>
            </div>`;
    }).join('');
    return el;
}

function _modelConfig(modelId, providers) {
    const el = document.createElement('div');
    el.innerHTML = `
        <p class="text-[11px] text-slate-400 leading-relaxed mb-3">
            A model is routable if at least one endpoint advertises it in its <code>models:</code>
            list (config.yaml / .env / UI / auto-discovery). The smart router filters the pool to
            those endpoints before scoring by success² / latency × cost_factor.
        </p>
        ${_kv('Model ID', modelId)}
        ${_kv('Advertised by', providers.join(', ') || '—')}
        ${_kv('Alias?', 'Check config.yaml → model_aliases (e.g. "fast", "cheap")')}
    `;
    return el;
}

function _modelRelated(candidates) {
    const el = document.createElement('div');
    if (!candidates.length) {
        el.innerHTML = '<p class="text-[11px] text-slate-600 font-mono">No endpoint advertises this model. The router will return a no-endpoint error.</p>';
        return el;
    }
    el.innerHTML = `
        <p class="text-[11px] text-slate-400 mb-3">Endpoints that can serve this model:</p>
        ${candidates.map(ep => `
            <div class="py-2 border-b border-white/[0.04] last:border-0 cursor-pointer hover:bg-white/[0.02] -mx-2 px-2 rounded"
                 data-drilldown="endpoint:${ep.id}">
                <div class="flex items-center justify-between">
                    <span class="text-[11px] font-mono text-white">${ep.id}</span>
                    <span class="text-[9px] font-mono ${ep.status === 'Live' ? 'text-emerald-400' : 'text-slate-500'}">${ep.status}</span>
                </div>
                <p class="text-[9px] font-mono text-slate-500 truncate">${ep.url}</p>
            </div>`).join('')}
    `;
    return el;
}

function _modelActions(modelId) {
    const el = document.createElement('div');
    el.innerHTML = `
        <p class="text-[11px] text-slate-400 leading-relaxed mb-3">
            To change which endpoints serve this model, edit the endpoint's <code>models:</code>
            list. Cloud: <code>config.yaml</code>. Local: <code>LLM_PROXY_ENDPOINT_&lt;NAME&gt;_MODELS</code>
            in <code>.env</code>. Auto-discovered entries re-populate on each probe cycle.
        </p>
        <p class="text-[11px] text-slate-500 font-mono">
            Smoke-test: <br>
            <code class="text-cyan-400">curl $URL/v1/chat/completions -d '{"model":"${modelId}", ...}'</code>
        </p>
    `;
    return el;
}

// ── Plugin kind ────────────────────────────────────────────────────────────

async function _pluginTabs(name) {
    const [plugins, stats] = await Promise.all([
        api.fetchPlugins().catch(() => ({ plugins: [] })),
        api.fetchPluginStats().catch(() => ({})),
    ]);
    // /api/v1/plugins can return {plugins: [...]} OR an array at some points —
    // accept both so the drilldown survives shape drift.
    const list = Array.isArray(plugins) ? plugins : (plugins.plugins || plugins.data || []);
    const p = list.find(x => x.name === name);
    if (!p) throw new Error(`Plugin '${name}' not found in pipeline.`);
    const s = (stats && (stats[name] || (stats.stats && stats.stats[name]))) || {};
    return {
        overview: () => _pluginOverview(p, s),
        timeline: () => _pluginTimeline(s),
        config: () => _pluginConfig(p),
        related: () => _pluginRelated(p, list),
        actions: () => _pluginActions(p),
    };
}

function _pluginOverview(p, stats) {
    const el = document.createElement('div');
    const enabled = p.enabled !== false;
    el.innerHTML = `
        <div class="mb-4">
            <span class="text-2xl font-black ${enabled ? 'text-emerald-400' : 'text-slate-500'}">${enabled ? 'ENABLED' : 'DISABLED'}</span>
            <span class="text-[10px] text-slate-500 ml-2 font-mono uppercase">Ring: ${p.hook || p.ring || '—'}</span>
        </div>
        ${_kv('Name', p.name)}
        ${_kv('Description', p.description || '—')}
        ${_kv('Fail policy', p.fail_policy || 'open')}
        ${_kv('Timeout', (p.timeout_ms || p.timeout || '—') + (p.timeout_ms ? ' ms' : ''))}
        ${_kv('Executions', stats.executions || stats.total || 0)}
        ${_kv('Avg latency', stats.avg_latency_ms ? `${stats.avg_latency_ms.toFixed(1)} ms` : '—')}
        ${_kv('Last error', stats.last_error || '—')}
    `;
    return el;
}

function _pluginTimeline(stats) {
    const el = document.createElement('div');
    const ev = stats.recent_executions || stats.recent || [];
    if (!ev.length) {
        el.innerHTML = '<p class="text-[11px] text-slate-600 font-mono">No recent executions recorded.</p>';
        return el;
    }
    el.innerHTML = ev.slice(0, 20).map(e => `
        <div class="py-2 border-b border-white/[0.04] last:border-0">
            <div class="flex items-center justify-between mb-0.5">
                <span class="text-[9px] font-mono text-slate-500">${_fmtTs(e.ts)}</span>
                <span class="text-[10px] font-mono ${e.ok === false ? 'text-rose-400' : 'text-emerald-400'}">${e.ok === false ? 'FAIL' : 'OK'}</span>
            </div>
            ${e.latency_ms != null ? `<span class="text-[10px] font-mono text-slate-500">${e.latency_ms.toFixed(1)}ms</span>` : ''}
            ${e.error ? `<p class="text-[10px] text-rose-400 font-mono mt-1">${e.error}</p>` : ''}
        </div>`).join('');
    return el;
}

function _pluginConfig(p) {
    const el = document.createElement('div');
    const cfg = p.config || p.settings || {};
    if (!Object.keys(cfg).length) {
        el.innerHTML = `
            <p class="text-[11px] text-slate-400 mb-3">
                Plugin has no declared config. Defaults applied from the marketplace manifest.
            </p>
            ${_kv('Entrypoint', p.entrypoint || '—')}
            ${_kv('Type', p.type || 'python')}
            ${_kv('Version', p.version || '—')}
        `;
        return el;
    }
    const rows = Object.entries(cfg).map(([k, v]) => _kv(k, typeof v === 'object' ? JSON.stringify(v) : v)).join('');
    el.innerHTML = `
        ${_kv('Entrypoint', p.entrypoint || '—')}
        ${_kv('Type', p.type || 'python')}
        <div class="mt-3">
            <h4 class="text-[10px] font-bold text-slate-500 uppercase tracking-widest mb-2">Runtime config</h4>
            ${rows}
        </div>
    `;
    return el;
}

function _pluginRelated(p, list) {
    const el = document.createElement('div');
    const siblings = list.filter(x => x.name !== p.name && (x.hook === p.hook || x.ring === p.ring)).slice(0, 10);
    if (!siblings.length) {
        el.innerHTML = `<p class="text-[11px] text-slate-600 font-mono">No other plugin in the ${p.hook || p.ring} ring.</p>`;
        return el;
    }
    el.innerHTML = `
        <p class="text-[11px] text-slate-400 mb-3">Other plugins in the same ring — they execute in sequence.</p>
        ${siblings.map(s => `
            <div class="py-2 border-b border-white/[0.04] last:border-0 cursor-pointer hover:bg-white/[0.02] -mx-2 px-2 rounded"
                 data-drilldown="plugin:${s.name}">
                <div class="flex items-center justify-between">
                    <span class="text-[11px] font-mono text-white">${s.name}</span>
                    <span class="text-[9px] font-mono ${s.enabled !== false ? 'text-emerald-400' : 'text-slate-500'}">${s.enabled !== false ? 'ACTIVE' : 'DISABLED'}</span>
                </div>
                <p class="text-[9px] font-mono text-slate-500 truncate">${s.description || ''}</p>
            </div>`).join('')}
    `;
    return el;
}

function _pluginActions(p) {
    const el = document.createElement('div');
    el.innerHTML = `<div class="flex flex-col gap-2"></div>`;
    const wrap = el.firstElementChild;

    const btn = (label, tone, handler) => {
        const b = document.createElement('button');
        b.type = 'button';
        b.textContent = label;
        b.className = `px-3 py-2 rounded-lg text-[11px] font-bold transition-colors border ${
            tone === 'danger' ? 'bg-rose-500/10 hover:bg-rose-500/20 text-rose-300 border-rose-500/30'
            : 'bg-white/5 hover:bg-white/10 text-slate-300 border-white/10'
        }`;
        b.addEventListener('click', handler);
        return b;
    };

    wrap.appendChild(btn(p.enabled === false ? 'Enable plugin' : 'Disable plugin', 'default', async () => {
        try {
            await api.togglePlugin(p.name, p.enabled === false);
            toast(`Plugin "${p.name}" toggled`, 'success');
        } catch (e) {
            toast(`Toggle failed: ${e.message}`, 'error');
        }
    }));
    wrap.appendChild(btn('Uninstall', 'danger', async () => {
        const { confirm } = await import('../src/ui');
        const ok = await confirm({
            title: 'Uninstall plugin',
            message: `Remove "${p.name}" from the pipeline? The proxy will hot-swap — in-flight requests finish through the old ring, new requests use the new one.`,
            confirmLabel: 'Uninstall',
            danger: true,
        });
        if (!ok) return;
        try {
            await api.uninstallPlugin(p.name);
            toast(`Plugin "${p.name}" uninstalled`, 'success');
        } catch (e) {
            toast(`Uninstall failed: ${e.message}`, 'error');
        }
    }));
    return el;
}

// ── Dispatcher ─────────────────────────────────────────────────────────────

const TABS = ['overview', 'timeline', 'config', 'related', 'actions'];

function _titleFor(kind, id) {
    if (kind === 'endpoint') return `Endpoint · ${id}`;
    if (kind === 'request') return `Request · ${id}`;
    if (kind === 'model') return `Model · ${id}`;
    if (kind === 'plugin') return `Plugin · ${id}`;
    return `${kind} · ${id}`;
}

async function _open(kind, id) {
    const { createDrawer } = await import('../src/ui');
    const handle = createDrawer({ title: _titleFor(kind, id), body: _loading(), width: 560 });

    let tabs;
    try {
        if (kind === 'endpoint') tabs = await _endpointTabs(id);
        else if (kind === 'request') tabs = await _requestTabs(id);
        else if (kind === 'model') tabs = await _modelTabs(id);
        else if (kind === 'plugin') tabs = await _pluginTabs(id);
        else {
            handle.setBody(_errorBody(`No drilldown available for '${kind}'.`));
            return;
        }
    } catch (e) {
        handle.setBody(_errorBody(e.message || String(e)));
        return;
    }

    let active = 'overview';
    const render = () => {
        const wrap = document.createElement('div');
        wrap.appendChild(_tabBar(TABS, active, (next) => {
            active = next;
            render();
        }));
        const content = document.createElement('div');
        try {
            const panel = tabs[active]();
            content.appendChild(panel instanceof Node ? panel : document.createTextNode(String(panel)));
        } catch (e) {
            content.appendChild(_errorBody(e.message || String(e)));
        }
        wrap.appendChild(content);
        handle.setBody(wrap);
    };
    render();
}

// ── Global click handler for [data-drilldown] ─────────────────────────────

function _attachGlobalHandler() {
    document.addEventListener('click', (ev) => {
        const el = ev.target.closest('[data-drilldown]');
        if (!el) return;
        // Let [data-explain] on the same element still win if more specific
        // — explain runs first in DOM order, so it wouldn't overlap here.
        ev.preventDefault();
        ev.stopPropagation();
        const raw = el.getAttribute('data-drilldown') || '';
        const [kind, ...rest] = raw.split(':');
        _open(kind, rest.join(':') || null);
    });
}

export function initDrilldown() {
    _attachGlobalHandler();
}

export const drilldown = {
    open: _open,
};
