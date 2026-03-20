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
            dot.style.transform = 'translateX(24px)';
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
            fBtn.className = `flex items-center gap-2 px-3 py-1.5 rounded-lg border text-[9px] font-bold uppercase tracking-widest transition-all shadow-sm ${enabled ? 'bg-sky-500/10 border-sky-500/30 text-sky-400' : 'bg-white/5 border-white/10 text-slate-500 hover:bg-white/10 hover:text-slate-300'}`;
            fBtn.innerHTML = `
                <div class="w-5 h-2.5 rounded-full flex items-center px-[2px] transition-all ${enabled ? 'bg-sky-500' : 'bg-slate-700 shadow-inner'}">
                    <div class="w-1.5 h-1.5 bg-white rounded-full transition-all shadow-sm ${enabled ? 'transform translate-x-2.5' : ''}"></div>
                </div>
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

    // Priority Steering toggle
    const prioBtn = document.getElementById('priority-mode-btn');
    if (prioBtn) {
        prioBtn.addEventListener('click', async () => {
            const nextState = !store.state.priorityMode;
            try {
                const res = await api.togglePriorityMode(nextState);
                store.update({ priorityMode: res.enabled });
                // Visual feedback
                const dot = prioBtn.querySelector('div');
                if (dot) {
                    if (nextState) {
                        prioBtn.classList.replace('bg-slate-800', 'bg-sky-500');
                        dot.classList.replace('bg-slate-500', 'bg-white');
                        dot.style.transform = 'translateX(12px)';
                    } else {
                        prioBtn.classList.replace('bg-sky-500', 'bg-slate-800');
                        dot.classList.replace('bg-white', 'bg-slate-500');
                        dot.style.transform = 'translateX(0)';
                    }
                }
            } catch (e) {
                console.warn('Priority toggle failed:', e);
            }
        });
    }
}
