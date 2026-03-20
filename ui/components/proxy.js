/**
 * Proxy Component
 */
import { store } from '../services/store.js';
import { api } from '../services/api.js';

export function renderProxy() {
    const { proxyEnabled, priorityMode, features } = store.state;
    
    // Update Indicators
    const indicator = document.getElementById('proxy-status-indicator');
    const btn = document.getElementById('proxy-toggle-btn');
    const dot = btn ? btn.querySelector('div') : null;
    
    if (btn && dot && indicator) {
        if (proxyEnabled) {
            btn.classList.replace('bg-slate-700', 'bg-sky-500');
            dot.style.transform = 'translateX(32px)';
            const statusLabel = indicator.querySelector('span:last-child');
            if (statusLabel) {
                statusLabel.className = "text-emerald-400 font-mono text-xs font-black uppercase tracking-tighter";
                statusLabel.textContent = "ACTIVE";
            }
        } else {
            btn.classList.replace('bg-sky-500', 'bg-slate-700');
            dot.style.transform = 'translateX(0)';
            const statusLabel = indicator.querySelector('span:last-child');
            if (statusLabel) {
                statusLabel.className = "text-red-400 font-mono text-xs font-black uppercase tracking-tighter";
                statusLabel.textContent = "STOPPED";
            }
        }
    }

    // Features
    const container = document.getElementById('feature-toggles');
    if (container) {
        container.innerHTML = '';
        Object.entries(features).forEach(([name, enabled]) => {
            const fBtn = document.createElement('button');
            fBtn.className = `flex items-center gap-2 px-3 py-1 rounded-full border text-[8px] font-black uppercase tracking-widest transition-all ${enabled ? 'bg-sky-500/10 border-sky-500/30 text-sky-400 font-bold' : 'bg-white/5 border-white/10 text-slate-600'}`;
            fBtn.innerHTML = `
                <div class="w-1.5 h-1.5 rounded-full ${enabled ? 'bg-sky-400 animate-pulse' : 'bg-slate-700'}"></div>
                ${name.replace(/_/g, ' ')}
            `;
            fBtn.onclick = () => api.toggleFeature(name, !enabled).then(f => store.update({ features: { ...features, [name]: f.enabled } }));
            container.appendChild(fBtn);
        });
    }
}

export function initProxy() {
    const btn = document.getElementById('proxy-toggle-btn');
    if (btn) {
        btn.addEventListener('click', async () => {
            const nextState = !store.state.proxyEnabled;
            const res = await api.toggleProxy(nextState);
            store.update({ proxyEnabled: res.enabled });
        });
    }
}
