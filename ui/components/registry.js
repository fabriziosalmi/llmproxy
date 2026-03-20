/**
 * Endpoints View — LLM endpoint registry with health status.
 */
import { store } from '../services/store.js';
import { api } from '../services/api.js';

export async function fetchRegistry() {
    try {
        const data = await api.fetchRegistry();
        store.update({ registry: data });
    } catch {}
}

export function initRegistry() {
    fetchRegistry();
}

export function renderRegistry() {
    const container = document.getElementById('registry-container');
    if (!container) return;

    const endpoints = store.state.registry || [];

    if (endpoints.length === 0) {
        container.innerHTML = `
            <div class="bg-white/[0.03] backdrop-blur-xl rounded-2xl border border-white/[0.06] p-12 text-center">
                <p class="text-sm text-slate-500">No endpoints registered</p>
                <p class="text-[10px] text-slate-600 mt-1">Endpoints appear here when discovered or manually added.</p>
            </div>
        `;
        return;
    }

    container.innerHTML = `
        <div class="bg-white/[0.03] backdrop-blur-xl rounded-2xl border border-white/[0.06] overflow-hidden">
            <table class="w-full">
                <thead>
                    <tr class="border-b border-white/[0.06]">
                        <th class="text-left text-[9px] font-bold text-slate-500 uppercase tracking-widest px-4 py-3">Endpoint</th>
                        <th class="text-left text-[9px] font-bold text-slate-500 uppercase tracking-widest px-4 py-3">Status</th>
                        <th class="text-left text-[9px] font-bold text-slate-500 uppercase tracking-widest px-4 py-3">Latency</th>
                        <th class="text-left text-[9px] font-bold text-slate-500 uppercase tracking-widest px-4 py-3">Type</th>
                        <th class="text-left text-[9px] font-bold text-slate-500 uppercase tracking-widest px-4 py-3">Priority</th>
                        <th class="text-right text-[9px] font-bold text-slate-500 uppercase tracking-widest px-4 py-3">Actions</th>
                    </tr>
                </thead>
                <tbody id="registry-body"></tbody>
            </table>
        </div>
    `;

    const tbody = document.getElementById('registry-body');
    endpoints.forEach(ep => {
        const statusColor = ep.status === 'Live' ? 'emerald' : ep.status === 'IGNORED' ? 'slate' : 'amber';
        const row = document.createElement('tr');
        row.className = 'border-b border-white/[0.04] hover:bg-white/[0.02] transition-colors';
        row.innerHTML = `
            <td class="px-4 py-3">
                <p class="text-[11px] font-bold text-white">${ep.name || ep.id}</p>
                <p class="text-[9px] text-slate-500 font-mono truncate max-w-xs">${ep.url}</p>
            </td>
            <td class="px-4 py-3">
                <span class="text-[9px] font-bold text-${statusColor}-400 bg-${statusColor}-500/10 px-2 py-0.5 rounded">${ep.status}</span>
            </td>
            <td class="px-4 py-3 text-[10px] font-mono text-slate-400">${ep.latency}</td>
            <td class="px-4 py-3 text-[10px] text-slate-400">${ep.type}</td>
            <td class="px-4 py-3 text-[10px] font-mono text-slate-400">${ep.priority}</td>
            <td class="px-4 py-3 text-right">
                <button data-action="toggle" data-id="${ep.id}" class="text-[9px] text-slate-500 hover:text-amber-400 px-2 py-1 rounded hover:bg-white/5 transition-colors">Toggle</button>
                <button data-action="delete" data-id="${ep.id}" class="text-[9px] text-slate-500 hover:text-rose-400 px-2 py-1 rounded hover:bg-white/5 transition-colors">Delete</button>
            </td>
        `;
        tbody.appendChild(row);
    });

    // Wire actions
    tbody.querySelectorAll('button[data-action]').forEach(btn => {
        btn.addEventListener('click', async () => {
            const id = btn.dataset.id;
            if (btn.dataset.action === 'toggle') {
                await api.toggleEndpoint(id);
            } else if (btn.dataset.action === 'delete') {
                if (confirm(`Delete endpoint ${id}?`)) {
                    await api.deleteEndpoint(id);
                }
            }
            fetchRegistry();
        });
    });
}
