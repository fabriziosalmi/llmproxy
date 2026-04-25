/**
 * Trust-by-explanation service.
 *
 * Any element carrying `data-explain="<kind>[:<id>]"` becomes clickable
 * (or focusable + Enter) and opens a drawer describing WHY the surface
 * shows what it shows: source, timestamp, rule, recent evidence, and a
 * pointer to the full drilldown.
 *
 * Supported kinds (MVP):
 *   firewall                 — ASGI WAF state + disabled reason
 *   guard:<name>             — injection/language/link guard state
 *   circuit:<endpoint_id>    — circuit breaker state + last transitions
 *   endpoint:<endpoint_id>   — brief endpoint status (full detail via drilldown)
 *   provider-count           — why we see N active providers
 *
 * Data is pulled from already-existing backend endpoints (no new routes).
 * Long-format detail (full audit trail, config) is delegated to drilldown.
 */

import { api } from './api.js';
import { store } from './store.js';

const BASE_URL = window.location.origin;

function _authFetch(path) {
    const token = localStorage.getItem('proxy_key') || '';
    const headers = token ? { 'Authorization': `Bearer ${token}` } : {};
    return fetch(`${BASE_URL}${path}`, { headers }).then(r => {
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

function _kvRow(label, value) {
    const safeVal = value == null || value === '' ? '—' : String(value);
    return `
        <div class="grid grid-cols-[110px_1fr] gap-2 py-1.5 border-b border-white/[0.04] last:border-0">
            <span class="text-[10px] font-bold text-slate-500 uppercase tracking-wide">${label}</span>
            <span class="text-[11px] text-white font-mono break-all">${safeVal}</span>
        </div>`;
}

function _section(title, bodyHtml) {
    return `
        <section class="mt-4 first:mt-0">
            <h3 class="text-[10px] font-bold text-slate-500 uppercase tracking-widest mb-2">${title}</h3>
            ${bodyHtml}
        </section>`;
}

function _loading() {
    const el = document.createElement('div');
    el.className = 'py-8 text-center text-[11px] text-slate-500 font-mono';
    el.textContent = 'Loading…';
    return el;
}

function _errorNode(msg) {
    const el = document.createElement('div');
    el.innerHTML = _section('Error', `<p class="text-[11px] text-rose-400 font-mono">${msg}</p>`);
    return el;
}

// ── Content builders per kind ───────────────────────────────────────────────

async function _renderFirewall() {
    const data = await api.fetchGuardsStatus();
    const fw = data.firewall || {};
    const enabled = fw.enabled !== false;
    const status = enabled ? 'ON' : 'OFF';
    const statusCls = enabled ? 'text-emerald-400' : 'text-rose-400';

    const sigs = Object.entries(fw.block_by_signature || {})
        .sort((a, b) => b[1] - a[1])
        .slice(0, 5);
    const sigsHtml = sigs.length
        ? sigs.map(([s, n]) => `
            <div class="flex justify-between py-1 text-[11px] font-mono">
                <span class="text-slate-400 truncate max-w-[280px]">${s}</span>
                <span class="text-rose-400 font-bold">${n}</span>
            </div>`).join('')
        : '<p class="text-[11px] text-slate-600 font-mono">No blocks recorded yet.</p>';

    const body = document.createElement('div');
    body.innerHTML = `
        ${_section('Status',
            `<p class="text-2xl font-black ${statusCls} mb-2">${status}</p>
             ${_kvRow('Source', enabled ? 'config (default)' : (fw.disabled_reason || 'config'))}
             ${_kvRow('Rule', 'security.firewall.enabled in config.yaml, LLM_PROXY_FIREWALL_ENABLED env override')}
             ${_kvRow('Signatures', `${fw.signatures_count || 0} loaded`)}
             ${_kvRow('Scanned', (fw.total_scanned || 0).toLocaleString())}
             ${_kvRow('Blocked', (fw.total_blocked || 0).toLocaleString())}
        `)}
        ${_section('Top blocked signatures', sigsHtml)}
        ${_section('How to change',
            `<p class="text-[11px] text-slate-400 leading-relaxed">
                Toggle via <code class="text-cyan-400">LLM_PROXY_FIREWALL_ENABLED=0</code> in <code>.env</code>
                or <code class="text-cyan-400">security.firewall.enabled: false</code> in
                <code>config.yaml</code>. Requires restart. The UI is intentionally read-only —
                a click-to-disable would make L1 injection defense trivially removable.
             </p>`)}
    `;
    return body;
}

async function _renderGuard(name) {
    const data = await api.fetchGuardsStatus();
    const features = data.features || {};
    const enabled = features[name] !== false;

    const body = document.createElement('div');
    body.innerHTML = `
        ${_section('Status',
            `<p class="text-2xl font-black ${enabled ? 'text-emerald-400' : 'text-slate-400'} mb-2">${enabled ? 'ACTIVE' : 'DISABLED'}</p>
             ${_kvRow('Guard', name)}
             ${_kvRow('Toggle', 'UI (Guards view) or POST /api/v1/features/toggle')}
             ${_kvRow('Persisted', 'SQLite state — survives restart')}
        `)}
        ${_section('What it does', `
            <p class="text-[11px] text-slate-400 leading-relaxed">${_GUARD_DESCRIPTIONS[name] || 'Security guard.'}</p>
        `)}
    `;
    return body;
}

const _GUARD_DESCRIPTIONS = {
    injection_guard: 'Regex threat scoring with 8 injection patterns. Blocks "ignore previous instructions", role-play attacks, system prompt extraction before the request reaches the upstream.',
    language_guard: 'Detects anomalous charsets, control characters, zero-width abuse, and steganography in LLM responses.',
    link_sanitizer: 'Strips blocked domains and suspicious URLs from prompts and responses. Prevents phishing and malicious link injection.',
    pii_masker: 'Dual-mode PII detection: Presidio NLP if installed, regex fallback otherwise. Masks emails, phones, SSNs, credit cards, IBANs.',
};

async function _renderCircuit(endpointId) {
    const data = await api.fetchGuardsStatus();
    const cb = (data.circuit_breakers || {})[endpointId] || {};
    const state = (cb.state || 'unknown').toLowerCase();
    const stateCls = state === 'closed' ? 'text-emerald-400'
        : state === 'open' ? 'text-rose-400' : state === 'half_open' ? 'text-amber-400' : 'text-slate-400';

    const body = document.createElement('div');
    body.innerHTML = `
        ${_section('State',
            `<p class="text-2xl font-black ${stateCls} mb-2 uppercase">${state}</p>
             ${_kvRow('Endpoint', endpointId)}
             ${_kvRow('Failures', `${cb.failure_count || 0}/${cb.failure_threshold || 5}`)}
             ${_kvRow('Rule', 'failure_threshold consecutive errors ⇒ OPEN for 60s ⇒ HALF_OPEN probe ⇒ CLOSED on success')}
             ${_kvRow('Source', 'core.circuit_breaker')}
        `)}
        ${_section('What this means', `
            <p class="text-[11px] text-slate-400 leading-relaxed">
                ${state === 'open' ? 'Upstream is being shielded from traffic after repeated failures. The prober will probe once the cooldown expires.' : ''}
                ${state === 'half_open' ? 'The breaker is admitting one probe request to test if the upstream has recovered.' : ''}
                ${state === 'closed' ? 'Requests flow normally. No failure pattern detected recently.' : ''}
            </p>
        `)}
        ${_section('Actions', `
            <p class="text-[11px] text-slate-500 font-mono">
                Manual reset: POST /api/v1/circuit-breaker/${encodeURIComponent(endpointId)}/reset
            </p>
        `)}
    `;
    return body;
}

async function _renderEndpoint(endpointId) {
    const registry = store.state.registry || [];
    const ep = registry.find(e => e.id === endpointId);
    if (!ep) return _errorNode(`Endpoint '${endpointId}' not found in registry.`);

    const body = document.createElement('div');
    body.innerHTML = `
        ${_section('Overview',
            `${_kvRow('ID', ep.id)}
             ${_kvRow('URL', ep.url)}
             ${_kvRow('Status', ep.status)}
             ${_kvRow('Latency', ep.latency || '—')}
             ${_kvRow('Priority', ep.priority)}
             ${_kvRow('Type', ep.type || 'Generic')}
             ${_kvRow('Circuit', `${(ep.circuit_state || 'closed').toUpperCase()} (${ep.failure_count || 0}/${ep.failure_threshold || 5})`)}
        `)}
        ${_section('Source of truth', `
            <p class="text-[11px] text-slate-400 leading-relaxed">
                Endpoints can come from config.yaml, .env (LLM_PROXY_ENDPOINT_*), the UI Add form, or auto-discovery.
                The badge tag (<code>config</code>/<code>env</code>/<code>ui</code>/<code>auto-discovery</code>) in the banner reflects the source.
            </p>
        `)}
        ${_section('Full detail', `
            <p class="text-[11px] text-slate-500 font-mono">Use the Endpoints table "Inspect" action for the full drilldown.</p>
        `)}
    `;
    return body;
}

async function _renderProviderCount() {
    const registry = store.state.registry || [];
    const active = registry.length;
    const bySource = {};
    for (const ep of registry) {
        const src = (ep._source || 'config');
        bySource[src] = (bySource[src] || 0) + 1;
    }
    const rows = Object.entries(bySource)
        .map(([k, v]) => `<div class="flex justify-between py-1"><span class="text-[11px] text-slate-400 font-mono">${k}</span><span class="text-[11px] text-white font-mono">${v}</span></div>`)
        .join('');

    const body = document.createElement('div');
    body.innerHTML = `
        ${_section('Active providers', `
            <p class="text-2xl font-black text-white mb-2">${active}</p>
            ${rows || '<p class="text-[11px] text-slate-600 font-mono">None.</p>'}
        `)}
        ${_section('How to add more', `
            <p class="text-[11px] text-slate-400 leading-relaxed">
                Cloud: set <code>{PROVIDER}_API_KEY</code> in <code>.env</code>.<br>
                Local/self-hosted: <code>LLM_PROXY_ENDPOINT_{NAME}_URL=…</code>.<br>
                UI wizard: Endpoints → Add.<br>
                Auto-discovery probes 127.0.0.1 + <code>host.docker.internal</code> + LLM_PROXY_DISCOVERY_PEERS every 5min.
            </p>
        `)}
    `;
    return body;
}

// ── Dispatcher ─────────────────────────────────────────────────────────────

async function _buildContent(kind, id) {
    if (kind === 'firewall') return _renderFirewall();
    if (kind === 'guard' && id) return _renderGuard(id);
    if (kind === 'circuit' && id) return _renderCircuit(id);
    if (kind === 'endpoint' && id) return _renderEndpoint(id);
    if (kind === 'provider-count') return _renderProviderCount();
    return _errorNode(`No explanation available for '${kind}'.`);
}

function _titleFor(kind, id) {
    if (kind === 'firewall') return 'Why · ASGI Firewall';
    if (kind === 'guard') return `Why · ${id}`;
    if (kind === 'circuit') return `Why · Circuit · ${id}`;
    if (kind === 'endpoint') return `Why · Endpoint · ${id}`;
    if (kind === 'provider-count') return 'Why · Provider count';
    return 'Why';
}

async function _open(kind, id) {
    const { createDrawer } = await import('../src/ui');
    const handle = createDrawer({
        title: _titleFor(kind, id),
        body: _loading(),
    });
    try {
        const content = await _buildContent(kind, id);
        if (handle.isOpen) handle.setBody(content);
    } catch (e) {
        if (handle.isOpen) handle.setBody(_errorNode(e.message || String(e)));
    }
}

// ── Global click/keyboard handler for [data-explain] ───────────────────────

function _attachGlobalHandler() {
    // Delegated: survives re-renders and works for elements added after init.
    document.addEventListener('click', (ev) => {
        const el = ev.target.closest('[data-explain]');
        if (!el) return;
        ev.preventDefault();
        ev.stopPropagation();
        const [kind, ...rest] = (el.getAttribute('data-explain') || '').split(':');
        _open(kind, rest.join(':') || null);
    });
    document.addEventListener('keydown', (ev) => {
        if (ev.key !== 'Enter' && ev.key !== ' ') return;
        const el = ev.target.closest('[data-explain]');
        if (!el) return;
        // Space on buttons is already handled natively; skip to avoid double-open.
        if (ev.key === ' ' && el.tagName === 'BUTTON') return;
        ev.preventDefault();
        const [kind, ...rest] = (el.getAttribute('data-explain') || '').split(':');
        _open(kind, rest.join(':') || null);
    });
}

/**
 * Ensure any DOM element with [data-explain] is keyboard-focusable. Called
 * by view renderers after they stamp out elements with the attribute.
 */
export function markExplainable(root = document) {
    const nodes = root.querySelectorAll('[data-explain]');
    nodes.forEach(el => {
        // Clickable affordance (cursor + hover) without colliding with
        // existing button styles.
        if (!el.classList.contains('explain-target')) {
            el.classList.add('explain-target');
        }
        if (el.tagName !== 'BUTTON' && el.tagName !== 'A') {
            if (!el.hasAttribute('tabindex')) el.setAttribute('tabindex', '0');
            if (!el.hasAttribute('role')) el.setAttribute('role', 'button');
            if (!el.hasAttribute('aria-label')) {
                const kind = (el.getAttribute('data-explain') || '').split(':')[0];
                el.setAttribute('aria-label', `Explain ${kind}`);
            }
        }
    });
}

export function initExplain() {
    _attachGlobalHandler();
    // Initial sweep for attributes already in static HTML.
    markExplainable();
}

export const explain = {
    open: _open,
    markExplainable,
};
