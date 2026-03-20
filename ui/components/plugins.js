/**
 * Plugins View — Security plugin pipeline with per-plugin stats and config.
 */
import { api } from '../services/api.js';

const RING_COLORS = {
    ingress: 'rose',
    pre_flight: 'amber',
    routing: 'sky',
    post_flight: 'violet',
    background: 'teal',
};

export async function initPlugins() {
    await renderPluginList().catch(() => {});

    const btn = document.getElementById('reload-plugins-btn');
    if (btn) {
        btn.addEventListener('click', async () => {
            btn.textContent = 'Reloading...';
            btn.disabled = true;
            try {
                await fetch(`${window.location.origin}/api/v1/plugins/hot-swap`, { method: 'POST' });
                await renderPluginList();
            } catch (e) {
                console.error('Plugin reload failed:', e);
            }
            btn.textContent = 'Reload';
            btn.disabled = false;
        });
    }
}

async function renderPluginList() {
    const grid = document.getElementById('plugins-grid');
    if (!grid) return;

    try {
        const [data, stats] = await Promise.all([
            api.fetchPlugins(),
            api.fetchPluginStats().catch(() => ({})),
        ]);
        const plugins = data.plugins || data || [];
        grid.innerHTML = '';

        plugins.forEach(p => {
            const ring = (p.hook || 'unknown').toLowerCase();
            const color = RING_COLORS[ring] || 'slate';
            const enabled = p.enabled !== false;
            const s = stats[p.name] || {};
            const inv = s.invocations || 0;
            const errs = s.errors || 0;
            const blocks = s.blocks || 0;
            const timeouts = s.timeouts || 0;
            const avgLat = s.avg_latency_ms || 0;
            const hasStats = inv > 0;
            const errRate = inv > 0 ? ((errs / inv) * 100).toFixed(1) : '0.0';

            const card = document.createElement('div');
            card.className = `bg-white/[0.03] backdrop-blur-xl rounded-2xl border border-white/[0.06] p-4 transition-all ${enabled ? '' : 'opacity-50'}`;
            card.innerHTML = `
                <div class="flex items-start justify-between mb-2">
                    <div class="flex-1 min-w-0">
                        <h3 class="text-[11px] font-bold text-white truncate">${p.name || 'Unknown'}</h3>
                        <div class="flex items-center gap-2 mt-1">
                            <span class="text-[8px] font-mono text-${color}-400 bg-${color}-500/10 px-1.5 py-0.5 rounded">${ring.toUpperCase()}</span>
                            <span class="text-[8px] font-mono text-slate-600">${p.timeout_ms || 500}ms</span>
                            <span class="text-[8px] font-mono text-slate-600">${p.fail_policy || 'open'}</span>
                        </div>
                    </div>
                    <div class="flex items-center gap-1.5 shrink-0">
                        ${p.version && p.version !== '0.0.0' ? `<span class="text-[8px] font-mono text-slate-500">v${p.version}</span>` : ''}
                        <div class="w-2 h-2 rounded-full ${enabled ? 'bg-emerald-400' : 'bg-slate-600'}"></div>
                    </div>
                </div>
                ${p.description ? `<p class="text-[9px] text-slate-500 mb-2 line-clamp-2">${p.description}</p>` : ''}
                <div class="grid grid-cols-4 gap-1 mt-2 pt-2 border-t border-white/[0.04]">
                    <div class="text-center">
                        <p class="text-[10px] font-bold font-mono ${hasStats ? 'text-white' : 'text-slate-600'}">${inv.toLocaleString()}</p>
                        <p class="text-[7px] text-slate-600 uppercase">calls</p>
                    </div>
                    <div class="text-center">
                        <p class="text-[10px] font-bold font-mono ${blocks > 0 ? 'text-rose-400' : 'text-slate-600'}">${blocks}</p>
                        <p class="text-[7px] text-slate-600 uppercase">blocks</p>
                    </div>
                    <div class="text-center">
                        <p class="text-[10px] font-bold font-mono ${errs > 0 ? 'text-amber-400' : 'text-slate-600'}">${errRate}%</p>
                        <p class="text-[7px] text-slate-600 uppercase">err</p>
                    </div>
                    <div class="text-center">
                        <p class="text-[10px] font-bold font-mono ${avgLat > 100 ? 'text-amber-400' : 'text-slate-600'}">${avgLat.toFixed(1)}</p>
                        <p class="text-[7px] text-slate-600 uppercase">ms</p>
                    </div>
                </div>
                ${renderConfigFields(p)}
            `;
            grid.appendChild(card);
        });
    } catch {
        grid.innerHTML = `
            <div class="col-span-2 flex flex-col items-center justify-center py-12 text-center">
                <div class="w-10 h-10 rounded-full bg-white/5 flex items-center justify-center mb-3">
                    <svg class="w-5 h-5 text-slate-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 9v2m0 4h.01M5.07 19h13.86c1.54 0 2.5-1.67 1.73-3L13.73 4c-.77-1.33-2.69-1.33-3.46 0L3.34 16c-.77 1.33.19 3 1.73 3z"/>
                    </svg>
                </div>
                <p class="text-[11px] font-bold text-slate-400 mb-1">Backend Offline</p>
                <p class="text-[9px] text-slate-600 font-mono">Start the gateway to load the plugin pipeline</p>
            </div>`;
    }
}

function renderConfigFields(plugin) {
    const schema = plugin.ui_schema;
    if (!schema || !Array.isArray(schema) || schema.length === 0) return '';

    const fields = schema.map(f => `
        <div class="flex items-center justify-between">
            <span class="text-[8px] text-slate-500">${f.label || f.key}</span>
            <span class="text-[8px] font-mono text-slate-400">${f.default !== undefined ? f.default : '--'}</span>
        </div>
    `).join('');

    return `
        <div class="mt-2 pt-2 border-t border-white/[0.04] space-y-1">
            <p class="text-[7px] text-slate-600 uppercase font-bold mb-1">Config</p>
            ${fields}
        </div>
    `;
}
