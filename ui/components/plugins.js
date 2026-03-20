/**
 * Plugins Component
 */
import { api } from '../services/api.js';

export async function initPlugins() {
    await renderPlugins();
    
    document.getElementById('reload-plugins').addEventListener('click', async () => {
        const btn = document.getElementById('reload-plugins');
        const originalText = btn.innerHTML;
        btn.innerHTML = '<svg class="w-3.5 h-3.5 animate-spin" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg> SWAPPING...';
        btn.disabled = true;
        
        try {
            // Hot-swap is triggered by any toggle, or we can add a specific endpoint
            // For now, let's just re-render
            await renderPlugins();
            setTimeout(() => {
                btn.innerHTML = originalText;
                btn.disabled = false;
            }, 1000);
        } catch (e) {
            btn.innerHTML = 'ERROR';
            btn.classList.add('text-rose-400');
        }
    });
}

export async function renderPlugins() {
    const grid = document.getElementById('plugin-grid');
    if (!grid) return;

    try {
        const response = await fetch('/api/v1/plugins');
        const data = await response.json();
        const plugins = data.plugins || [];

        grid.innerHTML = plugins.map(p => `
            <div class="group relative bg-[#131316] border border-white/[0.05] hover:border-orange-500/30 rounded-2xl p-5 transition-all duration-300 shadow-xl overflow-hidden">
                <!-- Background Glow -->
                <div class="absolute -right-10 -top-10 w-32 h-32 bg-orange-500/5 rounded-full blur-3xl opacity-0 group-hover:opacity-100 transition-opacity"></div>
                
                <div class="flex items-start justify-between mb-4 relative">
                    <div class="w-10 h-10 rounded-xl bg-white/[0.03] border border-white/10 flex items-center justify-center text-orange-400">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 3v2m6-2v2M9 19v2m6-2v2M5 9H3m2 6H3m18-6h-2m2 6h-2M7 19h10a2 2 0 002-2V7a2 2 0 00-2-2H7a2 2 0 00-2 2v10a2 2 0 002 2zM9 9h6v6H9V9z"/>
                        </svg>
                    </div>
                    <label class="relative inline-flex items-center cursor-pointer">
                        <input type="checkbox" class="sr-only peer" ${p.enabled ? 'checked' : ''} onchange="window.togglePlugin('${p.name}', this.checked)">
                        <div class="w-9 h-5 bg-white/5 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-slate-400 after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-orange-600 peer-checked:after:bg-white border border-white/10"></div>
                    </label>
                </div>

                <div class="relative">
                    <h3 class="text-sm font-bold text-white mb-1">${p.name}</h3>
                    <p class="text-[10px] text-slate-500 font-mono mb-4 uppercase tracking-widest">${p.entrypoint}</p>
                    
                    <div class="flex flex-wrap gap-2">
                        <span class="px-2 py-0.5 rounded-md bg-white/[0.03] border border-white/5 text-[9px] font-bold text-slate-400 uppercase tracking-tighter">Ring: ${p.hook}</span>
                        <span class="px-2 py-0.5 rounded-md bg-white/[0.03] border border-white/5 text-[9px] font-bold text-slate-400 uppercase tracking-tighter">Priority: ${p.priority}</span>
                        <span class="px-2 py-0.5 rounded-md bg-orange-500/10 border border-orange-500/20 text-[9px] font-bold text-orange-400 uppercase tracking-tighter">Python v3</span>
                    </div>
                </div>
            </div>
        `).join('');

        // Global function for toggle
        window.togglePlugin = async (name, enabled) => {
            await fetch('/api/v1/plugins/toggle', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name, enabled })
            });
            // Re-render to show state consistency if needed, but the UI is optimistic
        };

    } catch (e) {
        grid.innerHTML = '<div class="col-span-full text-center py-10 text-slate-500">Failed to load neural modules.</div>';
    }
}
