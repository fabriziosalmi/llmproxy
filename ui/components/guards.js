/**
 * Guards View — All security subsystem toggles (8 guards + master + priority).
 */
import { store } from '../services/store.js';
import { api } from '../services/api.js';
import { toast } from '../services/toast.js';

const GUARD_INFO = {
    injection_guard: {
        name: 'Injection Guard',
        icon: `<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"/></svg>`,
        desc: 'Regex threat scoring with 8 injection patterns. Blocks "ignore previous instructions", role-play attacks, system prompt extraction.',
        color: 'rose',
        toggleable: true,
    },
    language_guard: {
        name: 'Language Guard',
        icon: `<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 5h12M9 3v2m1.048 9.5A18.022 18.022 0 016.412 9m6.088 9h7M11 21l5-10 5 10M12.751 5C11.783 10.77 8.07 15.61 3 18.129"/></svg>`,
        desc: 'Detects anomalous charsets, control characters, zero-width abuse, and steganography in LLM responses.',
        color: 'amber',
        toggleable: true,
    },
    link_sanitizer: {
        name: 'Link Sanitizer',
        icon: `<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1"/></svg>`,
        desc: 'Strips blocked domains and suspicious URLs from prompts and responses. Prevents phishing and malicious link injection.',
        color: 'sky',
        toggleable: true,
    },
    pii_masker: {
        name: 'PII Masker',
        icon: `<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13.875 18.825A10.05 10.05 0 0112 19c-4.478 0-8.268-2.943-9.543-7a9.97 9.97 0 011.563-3.029m5.858.908a3 3 0 114.243 4.243M9.878 9.878l4.242 4.242M9.88 9.88l-3.29-3.29m7.532 7.532l3.29 3.29M3 3l3.59 3.59m0 0A9.953 9.953 0 0112 5c4.478 0 8.268 2.943 9.543 7a10.025 10.025 0 01-4.132 5.411m0 0L21 21"/></svg>`,
        desc: 'Dual-mode PII detection: Presidio NLP (opt-in) + regex fallback. Masks emails, phones, SSNs, credit cards, IBANs.',
        color: 'violet',
        toggleable: false,
        status: 'ALWAYS ON',
    },
    firewall: {
        name: 'ASGI Firewall',
        icon: `<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17.657 18.657A8 8 0 016.343 7.343S7 9 9 10c0-2 .5-5 2.986-7C14 5 16.09 5.777 17.656 7.343A7.975 7.975 0 0120 13a7.975 7.975 0 01-2.343 5.657z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.879 16.121A3 3 0 1012.015 11L11 14H9c0 .768.293 1.536.879 2.121z"/></svg>`,
        desc: 'Byte-level ASGI middleware. 11 banned injection signatures scanned at L7 before any route handler. Kill-switch capable.',
        color: 'red',
        toggleable: false,
        status: 'ALWAYS ON',
    },
    rate_limiter: {
        name: 'Rate Limiter',
        icon: `<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>`,
        desc: 'Per-IP and per-API-key token bucket with auto-eviction. Configurable burst capacity and Retry-After headers.',
        color: 'cyan',
        toggleable: false,
        status: 'MIDDLEWARE',
    },
    zero_trust: {
        name: 'Zero Trust',
        icon: `<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z"/></svg>`,
        desc: 'mTLS certificate validation + Tailscale identity verification. Per-request identity context injection into upstream headers.',
        color: 'indigo',
        toggleable: false,
        status: 'CONFIG',
    },
    circuit_breaker: {
        name: 'Circuit Breaker',
        icon: `<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/></svg>`,
        desc: 'Per-endpoint failure tracking. Auto-opens after 5 failures, recovers via half-open probe after 60s cooldown.',
        color: 'orange',
        toggleable: false,
        status: 'AUTO',
    },
};

export function initGuards() {
    renderGuards();
    initProxyToggle();
    initPriorityToggle();
    initOperations();
    refreshCacheStats();
    store.poll(refreshCacheStats, 10000, 'guards');
}

function initOperations() {
    const ops = [
        { id: 'reset-firewall-btn', fn: () => api.resetFirewall(), label: 'WAF counters reset' },
        { id: 'clear-caches-btn', fn: () => api.clearCaches(), label: 'Caches cleared' },
        { id: 'reset-security-btn', fn: () => api.resetSecurity(), label: 'Sessions & ledger reset' },
        { id: 'reload-config-btn', fn: () => api.reloadConfig(), label: 'Config reloaded' },
    ];
    for (const op of ops) {
        const btn = document.getElementById(op.id);
        if (!btn) continue;
        btn.addEventListener('click', async () => {
            btn.disabled = true;
            btn.textContent = 'Working...';
            try {
                await op.fn();
                toast(op.label, 'success');
            } catch (e) {
                toast(`Failed: ${e.message}`, 'error');
            }
            btn.disabled = false;
            btn.textContent = btn.textContent === 'Working...' ? op.label.split(' ')[0] : btn.textContent;
            // Restore original text
            const origTexts = { 'reset-firewall-btn': 'Reset WAF Counters', 'clear-caches-btn': 'Clear Caches', 'reset-security-btn': 'Reset Sessions & Ledger', 'reload-config-btn': 'Reload Config' };
            btn.textContent = origTexts[op.id] || btn.textContent;
        });
    }
}

function initProxyToggle() {
    const btn = document.getElementById('proxy-toggle-btn');
    if (!btn) return;

    btn.addEventListener('click', async () => {
        const newState = !store.state.proxyEnabled;
        try {
            const res = await api.toggleProxy(newState);
            store.update({ proxyEnabled: res.enabled });
            updateToggleUI('proxy-toggle-btn', 'proxy-toggle-dot', res.enabled, 'emerald');
        } catch (e) {
            console.error('Proxy toggle failed:', e);
        }
    });
}

function initPriorityToggle() {
    const btn = document.getElementById('priority-toggle-btn');
    if (!btn) return;

    btn.addEventListener('click', async () => {
        const newState = !store.state.priorityMode;
        try {
            const res = await api.togglePriorityMode(!store.state.priorityMode);
            store.update({ priorityMode: res.enabled });
            updateToggleUI('priority-toggle-btn', 'priority-toggle-dot', res.enabled, 'amber');
        } catch (e) {
            console.error('Priority toggle failed:', e);
        }
    });
}

function updateToggleUI(btnId, dotId, enabled, color) {
    const btn = document.getElementById(btnId);
    const dot = document.getElementById(dotId);
    if (!btn || !dot) return;

    btn.setAttribute('aria-checked', String(enabled));
    if (enabled) {
        btn.className = `relative w-14 h-7 rounded-full transition-colors bg-${color}-500/20 border border-${color}-500/30`;
        dot.className = `absolute top-0.5 left-0.5 w-6 h-6 rounded-full bg-${color}-400 transition-transform translate-x-7 shadow-lg shadow-${color}-500/30`;
    } else {
        btn.className = 'relative w-14 h-7 rounded-full transition-colors bg-slate-700/50 border border-slate-600/30';
        dot.className = 'absolute top-0.5 left-0.5 w-6 h-6 rounded-full bg-slate-500 transition-transform translate-x-0';
    }
}

export function renderGuards() {
    const grid = document.getElementById('guards-grid');
    if (!grid) return;

    const features = store.state.features || {};
    grid.innerHTML = '';

    for (const [key, info] of Object.entries(GUARD_INFO)) {
        const enabled = info.toggleable ? features[key] !== false : true;
        const c = info.color;

        const card = document.createElement('div');
        card.className = `bg-white/[0.03] backdrop-blur-xl rounded-2xl border ${enabled ? `border-${c}-500/20` : 'border-white/[0.06]'} p-5 transition-all`;
        card.innerHTML = `
            <div class="flex items-start justify-between mb-3">
                <div class="flex items-center gap-2">
                    <div class="text-${c}-400">${info.icon}</div>
                    <h3 class="text-xs font-bold text-white">${info.name}</h3>
                </div>
                ${info.toggleable ? `
                    <button data-guard="${key}" role="switch" aria-checked="${enabled}" aria-label="Toggle ${info.name}" class="guard-toggle relative w-10 h-5 rounded-full transition-colors ${enabled ? `bg-${c}-500/30 border border-${c}-500/40` : 'bg-slate-700/50 border border-slate-600/30'}">
                        <div class="absolute top-0.5 left-0.5 w-4 h-4 rounded-full transition-transform ${enabled ? `bg-${c}-400 translate-x-5` : 'bg-slate-500 translate-x-0'}"></div>
                    </button>
                ` : `
                    <span class="text-[10px] font-bold font-mono text-${c}-400/60 bg-${c}-500/10 px-2 py-0.5 rounded">${info.status}</span>
                `}
            </div>
            <p class="text-[10px] text-slate-400 leading-relaxed">${info.desc}</p>
            <div class="mt-3 flex items-center gap-2">
                <span class="text-[9px] font-mono ${enabled ? `text-${c}-400` : 'text-slate-600'}">${enabled ? 'ACTIVE' : 'DISABLED'}</span>
            </div>
        `;
        grid.appendChild(card);
    }

    // Wire toggles
    grid.querySelectorAll('.guard-toggle').forEach(btn => {
        btn.addEventListener('click', async () => {
            const name = btn.dataset.guard;
            const current = store.state.features[name] !== false;
            try {
                const res = await api.toggleFeature(name, !current);
                const features = { ...store.state.features, [name]: res.enabled };
                store.update({ features });
                renderGuards();
                toast(`${GUARD_INFO[name]?.name || name} ${res.enabled ? 'enabled' : 'disabled'}`, 'success');
            } catch (e) {
                toast(`Guard toggle failed: ${e.message}`, 'error');
            }
        });
    });

    // Update master toggles
    updateToggleUI('proxy-toggle-btn', 'proxy-toggle-dot', store.state.proxyEnabled, 'emerald');
    updateToggleUI('priority-toggle-btn', 'priority-toggle-dot', store.state.priorityMode || false, 'amber');
}

// ── Cache Stats ──

async function refreshCacheStats() {
    try {
        const data = await api.fetchCacheStats();
        renderCacheStats(data);
    } catch (e) {
        // API not available yet — show disabled state
        const badge = document.getElementById('cache-status-badge');
        if (badge) {
            badge.textContent = 'OFFLINE';
            badge.className = 'text-[10px] font-bold font-mono text-slate-500 bg-slate-500/10 px-2 py-0.5 rounded';
        }
    }
}

function renderCacheStats(data) {
    const badge = document.getElementById('cache-status-badge');
    const l1 = data.negative_cache || {};
    const l2 = data.positive_cache || {};

    // Status badge
    if (badge) {
        if (l1.enabled || l2.enabled) {
            badge.textContent = 'ACTIVE';
            badge.className = 'text-[10px] font-bold font-mono text-emerald-400 bg-emerald-500/10 px-2 py-0.5 rounded';
        } else {
            badge.textContent = 'DISABLED';
            badge.className = 'text-[10px] font-bold font-mono text-slate-500 bg-slate-500/10 px-2 py-0.5 rounded';
        }
    }

    // L1 Negative Cache
    const el = (id) => document.getElementById(id);
    if (l1.enabled) {
        if (el('l1-drops')) el('l1-drops').textContent = formatNumber(l1.drops || 0);
        if (el('l1-size')) el('l1-size').textContent = formatNumber(l1.size || 0);
        if (el('l1-maxsize')) el('l1-maxsize').textContent = formatNumber(l1.maxsize || 50000);
        if (el('l1-ttl')) el('l1-ttl').textContent = l1.ttl || 300;
    }

    // L2 Positive Cache
    if (l2.enabled) {
        const ratio = l2.hit_ratio || 0;
        if (el('l2-hit-ratio')) el('l2-hit-ratio').textContent = `${(ratio * 100).toFixed(1)}%`;
        if (el('l2-entries')) el('l2-entries').textContent = formatNumber(l2.entries || 0);
        if (el('l2-hits')) el('l2-hits').textContent = formatNumber(l2.hits || 0);
        if (el('l2-misses')) el('l2-misses').textContent = formatNumber(l2.misses || 0);
    }
}

function formatNumber(n) {
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
    if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
    return String(n);
}
