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

import { drawer } from './drawer.js';
import { api } from './api.js';
import { store } from './store.js';
import { dialog } from './dialog.js';
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
        const ok = await dialog.confirm({
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

function _requestTimeline(related, reqId) {
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

// ── Dispatcher ─────────────────────────────────────────────────────────────

const TABS = ['overview', 'timeline', 'config', 'related', 'actions'];

function _titleFor(kind, id) {
    if (kind === 'endpoint') return `Endpoint · ${id}`;
    if (kind === 'request') return `Request · ${id}`;
    return `${kind} · ${id}`;
}

async function _open(kind, id) {
    const handle = drawer.open({ title: _titleFor(kind, id), body: _loading(), width: 560 });

    let tabs;
    try {
        if (kind === 'endpoint') tabs = await _endpointTabs(id);
        else if (kind === 'request') tabs = await _requestTabs(id);
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
