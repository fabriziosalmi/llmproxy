/**
 * Plugins View — Security plugin pipeline management.
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
        const data = await api.fetchPlugins();
        const plugins = data.plugins || data || [];
        grid.innerHTML = '';

        plugins.forEach(p => {
            const ring = (p.hook || 'unknown').toLowerCase();
            const color = RING_COLORS[ring] || 'slate';
            const enabled = p.enabled !== false;

            const card = document.createElement('div');
            card.className = `bg-white/[0.03] backdrop-blur-xl rounded-2xl border border-white/[0.06] p-4 transition-all ${enabled ? '' : 'opacity-50'}`;
            card.innerHTML = `
                <div class="flex items-start justify-between mb-2">
                    <div>
                        <h3 class="text-[11px] font-bold text-white">${p.name || 'Unknown'}</h3>
                        <span class="text-[8px] font-mono text-${color}-400 bg-${color}-500/10 px-1.5 py-0.5 rounded mt-1 inline-block">${ring.toUpperCase()}</span>
                    </div>
                    <div class="flex items-center gap-1.5">
                        ${p.version ? `<span class="text-[8px] font-mono text-slate-500">${p.version}</span>` : ''}
                        <div class="w-2 h-2 rounded-full ${enabled ? 'bg-emerald-400' : 'bg-slate-600'}"></div>
                    </div>
                </div>
                <p class="text-[9px] text-slate-500 font-mono truncate">${p.entrypoint || ''}</p>
            `;
            grid.appendChild(card);
        });
    } catch {
        grid.innerHTML = '<p class="text-[10px] text-slate-500 italic col-span-2">Failed to load plugins.</p>';
    }
}
