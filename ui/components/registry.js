/**
 * Registry Component
 */
import { store } from '../services/store.js';
import { api } from '../services/api.js';

export async function fetchRegistry() {
    try {
        const data = await api.fetchRegistry();
        store.update({ registry: data });
    } catch (e) {
        // Backend unavailable
    }
}

export function initRegistry() {
    fetchRegistry();
}

// 2.10: Drag state
let dragSrcRow = null;

// 4.7: Context menu
let ctxMenu = null;
function showContextMenu(x, y, item) {
    hideContextMenu();
    ctxMenu = document.createElement('div');
    ctxMenu.className = 'fixed z-[100] w-48 bg-black/95 border border-white/10 rounded-xl shadow-2xl py-1 backdrop-blur-xl text-[10px] animate-in';
    ctxMenu.style.left = `${x}px`;
    ctxMenu.style.top = `${y}px`;
    const actions = [
        { label: '⚡ Toggle Endpoint', action: () => api.toggleEndpoint(item.id).then(fetchRegistry).catch(() => {}) },
        { label: '📋 Copy Provider ID', action: () => { navigator.clipboard.writeText(item.id || item.name).catch(() => {}); } },
        { label: '📊 View Latency', action: () => { alert(`Roundtrip: ${item.latency || '--ms'}`); } },
        { label: '🔺 Set Priority 1', action: () => api.updatePriority(item.id, 1).then(fetchRegistry).catch(() => {}) },
        { sep: true },
        { label: '🗑 Delete', danger: true, action: () => { if (confirm(`Delete ${item.name}?`)) api.deleteEndpoint(item.id).then(fetchRegistry).catch(() => {}); } },
    ];
    actions.forEach(a => {
        if (a.sep) {
            const hr = document.createElement('div');
            hr.className = 'my-1 border-t border-white/5';
            ctxMenu.appendChild(hr);
            return;
        }
        const btn = document.createElement('button');
        btn.className = `w-full text-left px-3 py-2 ${a.danger ? 'text-rose-400 hover:bg-rose-500/10' : 'text-slate-300 hover:bg-white/5'} transition-colors font-medium`;
        btn.textContent = a.label;
        btn.addEventListener('click', () => { a.action(); hideContextMenu(); });
        ctxMenu.appendChild(btn);
    });
    document.body.appendChild(ctxMenu);
    // Adjust if overflows viewport
    const rect = ctxMenu.getBoundingClientRect();
    if (rect.right > window.innerWidth) ctxMenu.style.left = `${window.innerWidth - rect.width - 8}px`;
    if (rect.bottom > window.innerHeight) ctxMenu.style.top = `${window.innerHeight - rect.height - 8}px`;
}
function hideContextMenu() {
    if (ctxMenu) { ctxMenu.remove(); ctxMenu = null; }
}
document.addEventListener('click', hideContextMenu);
document.addEventListener('contextmenu', (e) => {
    if (ctxMenu && !ctxMenu.contains(e.target)) hideContextMenu();
});

// 5.3: Slide-over Node Detail
let slideoverChart = null;

function openSlideover(item) {
    const panel = document.getElementById('node-slideover');
    const backdrop = document.getElementById('node-slideover-backdrop');
    if (!panel) return;

    // Fill data
    const title = document.getElementById('slideover-title');
    if (title) title.textContent = item.name || item.id || 'Provider';
    const status = document.getElementById('slideover-status');
    if (status) {
        status.textContent = item.status || '—';
        const s = (item.status || '').toLowerCase();
        status.className = `font-bold ${s === 'live' || s === 'verified' ? 'text-emerald-400' : s === 'error' ? 'text-rose-400' : 'text-slate-400'}`;
    }
    const type = document.getElementById('slideover-type');
    if (type) type.textContent = item.type || '—';
    const priority = document.getElementById('slideover-priority');
    if (priority) priority.textContent = item.priority || '—';
    const latency = document.getElementById('slideover-latency');
    if (latency) latency.textContent = item.latency || '--ms';

    // Health heatmap
    const healthEl = document.getElementById('slideover-health');
    if (healthEl && item.history) {
        healthEl.innerHTML = item.history.map(c => {
            const colors = { emerald: 'bg-emerald-500', amber: 'bg-amber-500', sky: 'bg-sky-500', slate: 'bg-slate-600', rose: 'bg-rose-500' };
            return `<div class="w-2 h-5 rounded-sm ${colors[c] || colors.slate}"></div>`;
        }).join('');
    }

    // Mini chart
    const canvas = document.getElementById('slideover-chart');
    if (canvas && typeof Chart !== 'undefined') {
        if (slideoverChart) slideoverChart.destroy();
        const fakeData = Array.from({ length: 15 }, () => Math.floor(Math.random() * 200 + 50));
        slideoverChart = new Chart(canvas, {
            type: 'line',
            data: {
                labels: fakeData.map((_, i) => `${i * 2}s`),
                datasets: [{
                    data: fakeData,
                    borderColor: '#38bdf8',
                    backgroundColor: 'rgba(56,189,248,0.08)',
                    fill: true,
                    tension: 0.4,
                    borderWidth: 1.5,
                    pointRadius: 0,
                }]
            },
            options: {
                plugins: { legend: { display: false } },
                scales: {
                    x: { display: false },
                    y: { display: true, grid: { color: 'rgba(255,255,255,0.03)' }, ticks: { color: '#475569', font: { size: 9 } } }
                },
                responsive: true,
                maintainAspectRatio: false,
            }
        });
    }

    // Actions
    const toggleBtn = document.getElementById('slideover-toggle');
    const deleteBtn = document.getElementById('slideover-delete');
    if (toggleBtn) {
        toggleBtn.onclick = () => api.toggleEndpoint(item.id).then(fetchRegistry).catch(() => {});
    }
    if (deleteBtn) {
        deleteBtn.onclick = () => {
            if (confirm(`Delete ${item.name}?`)) {
                api.deleteEndpoint(item.id).then(fetchRegistry).catch(() => {});
                closeSlideover();
            }
        };
    }

    // Open animation
    if (backdrop) { backdrop.classList.remove('hidden'); requestAnimationFrame(() => { backdrop.style.opacity = '1'; }); }
    panel.style.transform = 'translateX(0)';

    // Close handlers
    const closeBtn = document.getElementById('slideover-close');
    if (closeBtn) closeBtn.onclick = closeSlideover;
    if (backdrop) backdrop.onclick = closeSlideover;
}

function closeSlideover() {
    const panel = document.getElementById('node-slideover');
    const backdrop = document.getElementById('node-slideover-backdrop');
    if (panel) panel.style.transform = 'translateX(100%)';
    if (backdrop) {
        backdrop.style.opacity = '0';
        setTimeout(() => backdrop.classList.add('hidden'), 300);
    }
}

export function renderRegistry() {
    const { registry } = store.state;
    const tbody = document.getElementById('registry-table-body');
    if (!tbody || !registry.length) return;
    tbody.innerHTML = '';

    registry.forEach((item, idx) => {
        const tr = document.createElement('tr');
        tr.setAttribute('draggable', 'true');
        tr.dataset.idx = idx;

        const statusLower = String(item.status || '').toLowerCase();
        const isLive = statusLower === 'live' || statusLower === 'verified';
        const isDiscovered = statusLower === 'discovered';
        const isError = statusLower === 'error' || statusLower === 'ignored';
        const isDown = isError || statusLower === 'offline';

        tr.className = `group hover:bg-white/[0.02] transition-all ${isError ? 'bg-rose-500/[0.02]' : ''}`;

        // Badge colors
        let badgeBg = 'bg-white/5', badgeText = 'text-slate-500', badgeBorder = 'border-white/5';
        if (isLive) { badgeBg = 'bg-emerald-500/10'; badgeText = 'text-emerald-400'; badgeBorder = 'border-emerald-500/20'; }
        else if (isDiscovered) { badgeBg = 'bg-sky-500/10'; badgeText = 'text-sky-400'; badgeBorder = 'border-sky-500/20'; }
        else if (isError) { badgeBg = 'bg-rose-500/10'; badgeText = 'text-rose-400'; badgeBorder = 'border-rose-500/20'; }

        const dot = isLive ? 'bg-emerald-500' : (isError ? 'bg-rose-500' : 'bg-slate-500');

        // 2.7: Latency centered with monospace tabular nums
        const latencyDisplay = item.latency
            ? `<span class="font-mono tabular-nums">${item.latency}</span>`
            : `<span class="text-slate-600 font-mono">--ms</span>`;

        // Health heatmap
        if (!item.history) {
            item.history = Array.from({length: 10}, () => {
                if (isLive) return Math.random() > 0.1 ? 'emerald' : 'amber';
                if (isDiscovered) return Math.random() > 0.5 ? 'sky' : 'slate';
                return 'rose';
            });
        }

        // 2.6: Ignored/down nodes get empty outline squares, not filled red
        const heatmapHtml = item.history.map(c => {
            if (isDown) {
                return `<div class="w-1.5 h-3 rounded-[1px] border border-slate-700/60 bg-transparent" title="Offline"></div>`;
            }
            const colors = {
                emerald: 'bg-emerald-500 shadow-[0_0_2px_#10b981]',
                amber: 'bg-amber-500 shadow-[0_0_2px_#f59e0b]',
                sky: 'bg-sky-500 shadow-[0_0_2px_#0ea5e9]',
                slate: 'bg-slate-500/40',
            };
            return `<div class="w-1.5 h-3 rounded-[1px] ${colors[c] || colors.slate} transition-all cursor-help" title="Ping: ${Math.floor(Math.random()*200)}ms"></div>`;
        }).join('');

        tr.innerHTML = `
            <td class="p-2 pl-3 align-middle w-8">
                <div class="drag-handle cursor-grab active:cursor-grabbing text-slate-600 hover:text-slate-400 transition-colors" title="Drag to reorder">
                    <svg class="w-3.5 h-4" viewBox="0 0 10 16" fill="currentColor">
                        <circle cx="3" cy="2" r="1.2"/><circle cx="7" cy="2" r="1.2"/>
                        <circle cx="3" cy="6" r="1.2"/><circle cx="7" cy="6" r="1.2"/>
                        <circle cx="3" cy="10" r="1.2"/><circle cx="7" cy="10" r="1.2"/>
                    </svg>
                </div>
            </td>
            <td class="p-4 align-middle">
                <div class="flex items-center gap-3">
                    <div class="w-1.5 h-1.5 rounded-full ${dot} ${isLive ? 'animate-pulse' : ''} shadow-[0_0_8px_currentColor] shrink-0"></div>
                    <span class="text-xs font-bold text-slate-200 tracking-tight">${item.name}</span>
                </div>
            </td>
            <td class="p-4 align-middle text-center">
                <div class="relative group/priority inline-flex items-center gap-1 cursor-pointer">
                    <input type="number" value="${item.priority}" data-id="${item.id}"
                        class="priority-input w-10 bg-transparent border-0 text-[11px] text-center font-bold text-sky-400 font-mono outline-none hover:bg-white/5 focus:bg-white/5 focus:ring-1 focus:ring-sky-500/40 rounded transition-all tabular-nums">
                    <svg class="w-2.5 h-2.5 text-slate-600 opacity-0 group-hover/priority:opacity-100 transition-opacity shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z"/></svg>
                </div>
            </td>
            <td class="p-4 align-middle text-[10px] font-mono text-slate-500 font-medium">${item.type}</td>
            <td class="p-4 align-middle text-center">
                <span class="text-[11px] font-black text-sky-400">${latencyDisplay}</span>
            </td>
            <td class="p-4 align-middle">
                <div class="flex items-center gap-3 justify-center">
                    <span class="text-[8px] font-bold uppercase tracking-widest px-1.5 py-0.5 rounded ${badgeBg} ${badgeText} border ${badgeBorder} leading-none">${item.status}</span>
                    <div class="flex gap-[2px] items-center bg-black/40 p-[2px] rounded border border-white/5">
                        ${heatmapHtml}
                    </div>
                </div>
            </td>
            <td class="p-4 align-middle">
                <div class="flex items-center justify-end gap-1">
                    <button data-id="${item.id}" class="toggle-endpoint p-1.5 hover:bg-white/5 rounded-lg text-slate-500 hover:text-white transition-all ring-1 ring-transparent hover:ring-white/10" title="Toggle Endpoint">
                        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/></svg>
                    </button>
                    <button data-id="${item.id}" class="delete-endpoint p-1.5 hover:bg-rose-500/10 rounded-lg text-slate-500 hover:text-rose-400 transition-all ring-1 ring-transparent hover:ring-rose-500/20" title="Delete">
                        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
                    </button>
                </div>
            </td>
        `;

        // 2.10: Drag events
        tr.addEventListener('dragstart', (e) => {
            dragSrcRow = tr;
            tr.classList.add('opacity-40');
            e.dataTransfer.effectAllowed = 'move';
            e.dataTransfer.setData('text/plain', idx.toString());
        });
        tr.addEventListener('dragend', () => {
            tr.classList.remove('opacity-40');
            tbody.querySelectorAll('tr').forEach(r => r.classList.remove('border-t-2', 'border-sky-500/50'));
            dragSrcRow = null;
        });
        tr.addEventListener('dragover', (e) => {
            e.preventDefault();
            e.dataTransfer.dropEffect = 'move';
            tbody.querySelectorAll('tr').forEach(r => r.classList.remove('border-t-2', 'border-sky-500/50'));
            tr.classList.add('border-t-2', 'border-sky-500/50');
        });
        tr.addEventListener('drop', (e) => {
            e.preventDefault();
            tr.classList.remove('border-t-2', 'border-sky-500/50');
            const fromIdx = parseInt(e.dataTransfer.getData('text/plain'), 10);
            const toIdx = parseInt(tr.dataset.idx, 10);
            if (fromIdx === toIdx) return;

            // Reorder registry in store and reassign priorities
            const items = [...store.state.registry];
            const [moved] = items.splice(fromIdx, 1);
            items.splice(toIdx, 0, moved);
            items.forEach((it, i) => { it.priority = i + 1; });
            store.update({ registry: items });

            // Persist each new priority to backend (best-effort)
            items.forEach(it => {
                api.updatePriority(it.id, it.priority).catch(() => {});
            });
        });

        // 5.3: Click provider name to open slide-over
        const nameSpan = tr.querySelector('.text-slate-200.tracking-tight');
        if (nameSpan) {
            nameSpan.classList.add('cursor-pointer', 'hover:text-sky-400', 'transition-colors');
            nameSpan.addEventListener('click', (e) => {
                e.stopPropagation();
                openSlideover(item);
            });
        }

        // 4.7: Right-click context menu
        tr.addEventListener('contextmenu', (e) => {
            e.preventDefault();
            showContextMenu(e.clientX, e.clientY, item);
        });

        // Events
        tr.querySelector('.priority-input').addEventListener('change', (e) => api.updatePriority(item.id, e.target.value).then(fetchRegistry).catch(() => {}));
        tr.querySelector('.toggle-endpoint').addEventListener('click', () => api.toggleEndpoint(item.id).then(fetchRegistry).catch(() => {}));
        tr.querySelector('.delete-endpoint').addEventListener('click', () => {
            if (confirm(`Delete endpoint ${item.name || item.id}?`)) api.deleteEndpoint(item.id).then(fetchRegistry).catch(() => {});
        });

        tbody.appendChild(tr);
    });
}
