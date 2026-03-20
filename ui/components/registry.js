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
        tr.className = 'group hover:bg-white/[0.02] transition-colors';
        const isLive = item.status === 'Live' || item.status === 'verified';
        const color = isLive ? 'text-emerald-400' : 'text-slate-600';
        const dot = isLive ? 'bg-emerald-500' : 'bg-slate-700';
        
        tr.innerHTML = `
            <td class="p-5">
                <div class="flex items-center gap-3">
                    <div class="w-1.5 h-1.5 rounded-full ${dot} ${isLive ? 'animate-pulse' : ''}"></div>
                    <span class="text-xs font-bold text-white tracking-tight">${item.name}</span>
                </div>
            </td>
            <td class="p-5 text-center">
                <input type="number" value="${item.priority}" data-id="${item.id}" class="priority-input w-10 bg-white/5 border border-white/10 rounded-lg text-[10px] text-center font-bold text-sky-400 focus:border-sky-500 outline-none">
            </td>
            <td class="p-5 text-[10px] font-mono text-slate-500 font-medium">${item.type}</td>
            <td class="p-5 text-[10px] tabular-nums font-black text-sky-400">${item.latency}</td>
            <td class="p-5"><span class="text-[8px] font-black uppercase tracking-widest px-2 py-1 rounded bg-white/5 ${color} border border-white/5">${item.status}</span></td>
            <td class="p-5 text-right space-x-2">
                <button data-id="${item.id}" class="toggle-endpoint p-2 hover:bg-white/5 rounded-lg text-slate-500 hover:text-white transition-all" title="Toggle Endpoint">
                    <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/></svg>
                </button>
                <button data-id="${item.id}" class="delete-endpoint p-2 hover:bg-red-500/10 rounded-lg text-slate-700 hover:text-red-400 transition-all" title="Delete">
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
