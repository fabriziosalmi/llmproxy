/**
 * Registry Component
 */
import { store } from '../services/store.js';
import { api } from '../services/api.js';

export async function fetchRegistry() {
    const data = await api.fetchRegistry();
    store.update({ registry: data });
}

export function renderRegistry() {
    const { registry } = store.state;
    const tbody = document.getElementById('registry-table-body');
    if (!tbody) return;
    tbody.innerHTML = '';
    
    registry.forEach(item => {
        const tr = document.createElement('tr');
        
        const statusLower = String(item.status || '').toLowerCase();
        const isLive = statusLower === 'live' || statusLower === 'verified';
        const isDiscovered = statusLower === 'discovered';
        const isError = statusLower === 'error' || statusLower === 'ignored';

        // Contextual Glow State (FAANG feature #18)
        tr.className = `group hover:bg-white/[0.02] transition-all ${isError ? 'shadow-[inset_0_0_20px_rgba(225,29,72,0.1)] bg-rose-500/[0.02]' : ''}`;

        // Semantic badge colors
        let badgeBg = 'bg-white/5';
        let badgeText = 'text-slate-500';
        let badgeBorder = 'border-white/5';
        
        if (isLive) { badgeBg = 'bg-emerald-500/10'; badgeText = 'text-emerald-400'; badgeBorder = 'border-emerald-500/20'; }
        else if (isDiscovered) { badgeBg = 'bg-sky-500/10'; badgeText = 'text-sky-400'; badgeBorder = 'border-sky-500/20'; }
        else if (isError) { badgeBg = 'bg-rose-500/10'; badgeText = 'text-rose-400'; badgeBorder = 'border-rose-500/20'; }
        
        const dot = isLive ? 'bg-emerald-500' : (isError ? 'bg-rose-500' : 'bg-slate-500');
        const latencyDisplay = item.latency ? `${item.latency}ms` : `<span class="text-slate-600">--</span>`;

        // Generate randomized logit health heatmap (mocking previous 10 pings)
        if (!item.history) {
            item.history = Array.from({length: 10}, () => {
                if(isLive) return Math.random() > 0.1 ? 'emerald' : 'amber';
                if(isDiscovered) return Math.random() > 0.5 ? 'sky' : 'slate';
                return 'rose';
            });
        }
        const colorMap = {
            emerald: 'bg-emerald-500 text-emerald-500 shadow-[0_0_2px_#10b981]',
            amber: 'bg-amber-500 text-amber-500 shadow-[0_0_2px_#f59e0b]',
            sky: 'bg-sky-500 text-sky-500 shadow-[0_0_2px_#0ea5e9]',
            rose: 'bg-rose-500 text-rose-500 shadow-[0_0_2px_#e11d48]',
            slate: 'bg-slate-500/40 text-slate-500'
        };
        const heatmapHtml = item.history.map(c => `<div class="w-1.5 h-3 rounded-[1px] ${colorMap[c]} opacity-90 transition-all hover:opacity-100 cursor-help" title="Ping: ${Math.floor(Math.random()*200)}ms"></div>`).join('');

        tr.innerHTML = `
            <td class="p-5 align-middle">
                <div class="flex items-center gap-3">
                    <div class="w-1.5 h-1.5 rounded-full ${dot} ${isLive ? 'animate-pulse' : ''} shadow-[0_0_8px_currentColor]"></div>
                    <span class="text-xs font-bold text-slate-200 tracking-tight">${item.name}</span>
                </div>
            </td>
            <td class="p-5 align-middle text-center">
                <input type="number" value="${item.priority}" data-id="${item.id}" class="priority-input w-10 bg-white/5 border border-white/10 rounded-lg text-[10px] text-center font-bold text-sky-400 focus:border-sky-500 outline-none hover:bg-white/10 transition-colors">
            </td>
            <td class="p-5 align-middle text-[10px] font-mono text-slate-500 font-medium">${item.type}</td>
            <td class="p-5 align-middle text-[10px] tabular-nums font-black text-sky-400">${latencyDisplay}</td>
            <td class="p-5 align-middle">
                <div class="flex items-center justify-between w-24">
                    <span class="text-[8px] font-bold uppercase tracking-widest px-1.5 py-0.5 rounded ${badgeBg} ${badgeText} border ${badgeBorder}">${item.status}</span>
                    <div class="flex gap-[1px] items-center bg-black/40 p-[2px] rounded border border-white/5 opacity-80 hover:opacity-100 transition-opacity">
                        ${heatmapHtml}
                    </div>
                </div>
            </td>
            <td class="p-5 align-middle text-right space-x-2">
                <button data-id="${item.id}" class="toggle-endpoint p-2 hover:bg-white/5 rounded-lg text-slate-500 hover:text-white transition-all ring-1 ring-transparent hover:ring-white/10 shadow-sm" title="Toggle Endpoint">
                    <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/></svg>
                </button>
                <button data-id="${item.id}" class="delete-endpoint p-2 hover:bg-rose-500/10 rounded-lg text-slate-500 hover:text-rose-400 transition-all ring-1 ring-transparent hover:ring-rose-500/20 shadow-sm" title="Delete">
                    <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
                </button>
            </td>
        `;
        
        // Add events
        tr.querySelector('.priority-input').addEventListener('change', (e) => api.updatePriority(item.id, e.target.value).then(fetchRegistry));
        tr.querySelector('.toggle-endpoint').addEventListener('click', () => api.toggleEndpoint(item.id).then(fetchRegistry));
        tr.querySelector('.delete-endpoint').addEventListener('click', () => {
             if (confirm(`Delete immutable endpoint ${item.id}?`)) api.deleteEndpoint(item.id).then(fetchRegistry);
        });

        tbody.appendChild(tr);
    });
}
