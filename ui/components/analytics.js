/**
 * Analytics View — Spend breakdown by model and provider.
 * Fetches from GET /api/v1/analytics/spend and displays KPIs + tables.
 */
import { api } from '../services/api.js';
import { store } from '../services/store.js';

export function initAnalytics() {
    refreshAnalytics();
    store.poll(refreshAnalytics, 30000, 'analytics');
}

export function renderAnalytics() {
    // Called by store.subscribe — no-op, data refreshed via polling
}

async function refreshAnalytics() {
    try {
        const [byModel, byProvider] = await Promise.all([
            api.fetchSpend('model'),
            api.fetchSpend('provider'),
        ]);

        // Update KPIs from model breakdown totals
        const total = byModel.total || {};
        setText('kpi-spend-requests', (total.requests || 0).toLocaleString());
        setText('kpi-spend-total', '$' + (total.total_usd || 0).toFixed(4));
        setText('kpi-spend-prompt', (total.total_prompt_tokens || 0).toLocaleString());
        setText('kpi-spend-completion', (total.total_completion_tokens || 0).toLocaleString());

        renderBreakdown('analytics-by-model', 'Spend by Model', byModel.breakdown || [], 'model');
        renderBreakdown('analytics-by-provider', 'Spend by Provider', byProvider.breakdown || [], 'provider');
    } catch (e) {
        console.error('Failed to load analytics:', e);
    }
}

function renderBreakdown(containerId, title, rows, groupCol) {
    const container = document.getElementById(containerId);
    if (!container) return;

    if (!rows.length) {
        container.innerHTML = `
            <h3 class="text-xs font-bold text-white mb-4">${title}</h3>
            <p class="text-[9px] text-slate-600 text-center py-6">No spend data yet. Make some requests through the proxy.</p>
        `;
        return;
    }

    container.innerHTML = `
        <h3 class="text-xs font-bold text-white mb-4">${title}</h3>
        <div class="overflow-hidden rounded-xl">
            <table class="w-full">
                <thead>
                    <tr class="border-b border-white/[0.08]">
                        <th class="text-left text-[9px] font-bold text-slate-500 uppercase tracking-widest px-3 py-2">${groupCol}</th>
                        <th class="text-right text-[9px] font-bold text-slate-500 uppercase tracking-widest px-3 py-2">Requests</th>
                        <th class="text-right text-[9px] font-bold text-slate-500 uppercase tracking-widest px-3 py-2">Cost</th>
                        <th class="text-right text-[9px] font-bold text-slate-500 uppercase tracking-widest px-3 py-2">Avg Latency</th>
                    </tr>
                </thead>
                <tbody>
                    ${rows.map(r => {
                        const name = r[groupCol] || 'unknown';
                        const cost = (r.total_cost_usd || 0);
                        const costColor = cost > 1 ? 'text-rose-400' : cost > 0.1 ? 'text-amber-400' : 'text-emerald-400';
                        return `
                        <tr class="border-b border-white/[0.04] hover:bg-white/[0.02] transition-colors">
                            <td class="px-3 py-2"><span class="text-[10px] font-bold text-white font-mono">${name}</span></td>
                            <td class="px-3 py-2 text-right"><span class="text-[10px] font-mono text-sky-400">${(r.requests || 0).toLocaleString()}</span></td>
                            <td class="px-3 py-2 text-right"><span class="text-[10px] font-mono ${costColor}">$${cost.toFixed(4)}</span></td>
                            <td class="px-3 py-2 text-right"><span class="text-[10px] font-mono text-slate-400">${(r.avg_latency_ms || 0).toFixed(0)}ms</span></td>
                        </tr>`;
                    }).join('')}
                </tbody>
            </table>
        </div>
    `;
}

function setText(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
}
