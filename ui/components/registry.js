/**
 * Endpoints View — LLM endpoint registry with circuit breaker state.
 */
import { store } from '../services/store.js';
import { api } from '../services/api.js';
import { toast } from '../services/toast.js';

export async function fetchRegistry() {
    try {
        const data = await api.fetchRegistry();
        store.update({ registry: data });
    } catch {}
}

export function initRegistry() {
    fetchRegistry();
}

const CIRCUIT_STATES = {
    closed: { label: 'CLOSED', dot: 'bg-emerald-400 shadow-emerald-500/40', text: 'text-emerald-400' },
    open: { label: 'OPEN', dot: 'bg-rose-400 shadow-rose-500/40 animate-pulse', text: 'text-rose-400' },
    half_open: { label: 'HALF', dot: 'bg-amber-400 shadow-amber-500/40', text: 'text-amber-400' },
};

let _sortKey = 'priority';
let _sortAsc = false;

function sortEndpoints(endpoints) {
    const sorted = [...endpoints];
    sorted.sort((a, b) => {
        let va = a[_sortKey] ?? '', vb = b[_sortKey] ?? '';
        if (typeof va === 'string') va = va.toLowerCase();
        if (typeof vb === 'string') vb = vb.toLowerCase();
        if (va < vb) return _sortAsc ? -1 : 1;
        if (va > vb) return _sortAsc ? 1 : -1;
        return 0;
    });
    return sorted;
}

export function renderRegistry() {
    const container = document.getElementById('registry-container');
    if (!container) return;

    const endpoints = sortEndpoints(store.state.registry || []);

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
                        <th data-sort="id" class="text-left text-[9px] font-bold text-slate-500 uppercase tracking-widest px-4 py-3 cursor-pointer hover:text-white transition-colors">Endpoint ${_sortKey === 'id' ? (_sortAsc ? '&#9650;' : '&#9660;') : ''}</th>
                        <th data-sort="status" class="text-left text-[9px] font-bold text-slate-500 uppercase tracking-widest px-4 py-3 cursor-pointer hover:text-white transition-colors">Status ${_sortKey === 'status' ? (_sortAsc ? '&#9650;' : '&#9660;') : ''}</th>
                        <th data-sort="circuit_state" class="text-left text-[9px] font-bold text-slate-500 uppercase tracking-widest px-4 py-3 cursor-pointer hover:text-white transition-colors">Circuit ${_sortKey === 'circuit_state' ? (_sortAsc ? '&#9650;' : '&#9660;') : ''}</th>
                        <th data-sort="latency" class="text-left text-[9px] font-bold text-slate-500 uppercase tracking-widest px-4 py-3 cursor-pointer hover:text-white transition-colors">Latency ${_sortKey === 'latency' ? (_sortAsc ? '&#9650;' : '&#9660;') : ''}</th>
                        <th data-sort="priority" class="text-left text-[9px] font-bold text-slate-500 uppercase tracking-widest px-4 py-3 cursor-pointer hover:text-white transition-colors">Priority ${_sortKey === 'priority' ? (_sortAsc ? '&#9650;' : '&#9660;') : ''}</th>
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
        const circuit = CIRCUIT_STATES[(ep.circuit_state || 'closed').toLowerCase()] || CIRCUIT_STATES.closed;

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
            <td class="px-4 py-3">
                <div class="flex items-center gap-1.5">
                    <div class="w-2 h-2 rounded-full ${circuit.dot} shadow-[0_0_6px]"></div>
                    <span class="text-[9px] font-bold font-mono ${circuit.text}">${circuit.label}</span>
                    ${(ep.failure_count || 0) > 0 ? `<span class="text-[10px] font-mono text-slate-600">${ep.failure_count}/${ep.failure_threshold || 5}</span>` : ''}
                </div>
            </td>
            <td class="px-4 py-3 text-[10px] font-mono text-slate-400">${ep.latency || '--'}</td>
            <td class="px-4 py-3">
                <div class="flex items-center gap-1">
                    <button data-action="priority-down" data-id="${ep.id}" class="text-slate-600 hover:text-white p-0.5 rounded hover:bg-white/5 transition-colors">
                        <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
                    </button>
                    <span class="text-[10px] font-mono text-slate-400 w-4 text-center">${ep.priority}</span>
                    <button data-action="priority-up" data-id="${ep.id}" class="text-slate-600 hover:text-white p-0.5 rounded hover:bg-white/5 transition-colors">
                        <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 15l7-7 7 7"/></svg>
                    </button>
                </div>
            </td>
            <td class="px-4 py-3 text-right">
                <button data-action="reset-cb" data-id="${ep.id}" class="text-[9px] text-slate-500 hover:text-emerald-400 px-2 py-1 rounded hover:bg-white/5 transition-colors">Reset CB</button>
                <button data-action="toggle" data-id="${ep.id}" class="text-[9px] text-slate-500 hover:text-amber-400 px-2 py-1 rounded hover:bg-white/5 transition-colors">Toggle</button>
                <button data-action="delete" data-id="${ep.id}" class="text-[9px] text-slate-500 hover:text-rose-400 px-2 py-1 rounded hover:bg-white/5 transition-colors">Delete</button>
            </td>
        `;
        tbody.appendChild(row);
    });

    // Wire sort headers
    container.querySelectorAll('th[data-sort]').forEach(th => {
        th.addEventListener('click', () => {
            const key = th.dataset.sort;
            if (_sortKey === key) { _sortAsc = !_sortAsc; } else { _sortKey = key; _sortAsc = true; }
            renderRegistry();
        });
    });

    // Wire actions
    tbody.querySelectorAll('button[data-action]').forEach(btn => {
        btn.addEventListener('click', async () => {
            const id = btn.dataset.id;
            const action = btn.dataset.action;
            try {
                if (action === 'reset-cb') {
                    await api.resetCircuitBreaker(id);
                    toast(`Circuit breaker ${id} reset to CLOSED`, 'success');
                } else if (action === 'toggle') {
                    await api.toggleEndpoint(id);
                    toast(`Endpoint ${id} toggled`, 'success');
                } else if (action === 'delete') {
                    if (!confirm(`Delete endpoint ${id}?`)) return;
                    await api.deleteEndpoint(id);
                    toast(`Endpoint ${id} deleted`, 'success');
                } else if (action === 'priority-up') {
                    const ep = endpoints.find(e => e.id === id);
                    if (ep) await api.updatePriority(id, (ep.priority || 0) + 1);
                } else if (action === 'priority-down') {
                    const ep = endpoints.find(e => e.id === id);
                    if (ep) await api.updatePriority(id, Math.max(0, (ep.priority || 0) - 1));
                }
            } catch (e) {
                toast(`Action failed: ${e.message}`, 'error');
            }
            fetchRegistry();
        });
    });
}
