/**
 * Guards View — Security shield toggles with per-guard descriptions.
 */
import { store } from '../services/store.js';
import { api } from '../services/api.js';

const GUARD_INFO = {
    injection_guard: {
        name: 'Injection Guard',
        icon: '🛡',
        desc: 'Blocks prompt injection patterns via regex threat scoring. Detects "ignore previous instructions", role-play attacks, and system prompt extraction.',
        color: 'rose',
    },
    language_guard: {
        name: 'Language Guard',
        icon: '🔤',
        desc: 'Detects anomalous charsets and control characters in LLM responses. Blocks steganography, zero-width character abuse, and gibberish output.',
        color: 'amber',
    },
    link_sanitizer: {
        name: 'Link Sanitizer',
        icon: '🔗',
        desc: 'Strips blocked domains and suspicious URLs from prompts and responses. Prevents phishing and malicious link injection.',
        color: 'sky',
    },
};

export function initGuards() {
    renderGuards();
    initProxyToggle();
}

function initProxyToggle() {
    const btn = document.getElementById('proxy-toggle-btn');
    if (!btn) return;

    btn.addEventListener('click', async () => {
        const newState = !store.state.proxyEnabled;
        try {
            const res = await api.toggleProxy(newState);
            store.update({ proxyEnabled: res.enabled });
            updateProxyToggleUI(res.enabled);
        } catch (e) {
            console.error('Proxy toggle failed:', e);
        }
    });
}

function updateProxyToggleUI(enabled) {
    const btn = document.getElementById('proxy-toggle-btn');
    const dot = document.getElementById('proxy-toggle-dot');
    if (!btn || !dot) return;

    if (enabled) {
        btn.className = 'relative w-14 h-7 rounded-full transition-colors bg-emerald-500/20 border border-emerald-500/30';
        dot.className = 'absolute top-0.5 left-0.5 w-6 h-6 rounded-full bg-emerald-400 transition-transform translate-x-7 shadow-lg shadow-emerald-500/30';
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
        const enabled = features[key] !== false;
        const colorClass = info.color;

        const card = document.createElement('div');
        card.className = `bg-white/[0.03] backdrop-blur-xl rounded-2xl border ${enabled ? `border-${colorClass}-500/20` : 'border-white/[0.06]'} p-5 transition-all`;
        card.innerHTML = `
            <div class="flex items-start justify-between mb-3">
                <div class="flex items-center gap-2">
                    <span class="text-lg">${info.icon}</span>
                    <h3 class="text-xs font-bold text-white">${info.name}</h3>
                </div>
                <button data-guard="${key}" class="guard-toggle relative w-10 h-5 rounded-full transition-colors ${enabled ? `bg-${colorClass}-500/30 border border-${colorClass}-500/40` : 'bg-slate-700/50 border border-slate-600/30'}">
                    <div class="absolute top-0.5 left-0.5 w-4 h-4 rounded-full transition-transform ${enabled ? `bg-${colorClass}-400 translate-x-5` : 'bg-slate-500 translate-x-0'}"></div>
                </button>
            </div>
            <p class="text-[10px] text-slate-400 leading-relaxed">${info.desc}</p>
            <div class="mt-3 flex items-center gap-2">
                <span class="text-[9px] font-mono ${enabled ? `text-${colorClass}-400` : 'text-slate-600'}">${enabled ? 'ACTIVE' : 'DISABLED'}</span>
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
            } catch (e) {
                console.error('Guard toggle failed:', e);
            }
        });
    });

    // Update proxy toggle UI
    updateProxyToggleUI(store.state.proxyEnabled);
}
