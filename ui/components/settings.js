/**
 * Settings Component
 */
import { store } from '../services/store.js';
import { api } from '../services/api.js';

export function renderSettings() {
    const { features } = store.state;
    const container = document.querySelector('#view-settings .space-y-4');
    if (!container) return;
    
    container.innerHTML = '';
    Object.entries(features).forEach(([name, enabled]) => {
        const item = document.createElement('div');
        item.className = "glass p-6 rounded-3xl border border-white/5 flex items-center justify-between group hover:bg-white/[0.04] transition-all";
        item.innerHTML = `
            <div class="flex gap-5 items-center">
                <div class="p-3 bg-white/5 rounded-2xl text-slate-400 group-hover:text-sky-400 transition-colors">
                    <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/>
                    </svg>
                </div>
                <div>
                    <h4 class="text-sm font-bold text-white mb-0.5 uppercase tracking-tight">${name.replace(/_/g, ' ')}</h4>
                    <p class="text-[10px] text-slate-500">Autonomous neural hardening and adaptive routing management.</p>
                </div>
            </div>
            <button class="toggle-feature w-11 h-6 ${enabled ? 'bg-sky-500' : 'bg-slate-800'} rounded-full flex items-center px-1 transition-all">
                <div class="w-4 h-4 bg-white rounded-full shadow-md ${enabled ? 'ml-auto' : ''} transition-all"></div>
            </button>
        `;
        
        item.querySelector('.toggle-feature').onclick = () => api.toggleFeature(name, !enabled).then(f => store.update({ features: { ...features, [name]: f.enabled } }));
        container.appendChild(item);
    });
}
