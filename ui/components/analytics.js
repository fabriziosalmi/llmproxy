/**
 * Analytics View — Spend breakdown by model and provider.
 * Fetches from GET /api/v1/analytics/spend and displays KPIs + tables.
 */
import { api } from '../services/api.js';
import { store } from '../services/store.js';
import { downloadText, rowsToCsv, stamp } from '../services/file_actions.js';

export function initAnalytics() {
    refreshAnalytics();
    store.poll(refreshAnalytics, 30000, 'analytics');
}

export function renderAnalytics() {
    // Called by store.subscribe — no-op, data refreshed via polling
}

async function refreshAnalytics() {
    try {
        const [byModel, byProvider, efficiency] = await Promise.all([
            api.fetchSpend('model'),
            api.fetchSpend('provider'),
            _fetchEfficiency(),
        ]);

        const total = byModel.total || {};
        setText('kpi-spend-requests', (total.requests || 0).toLocaleString());
        setText('kpi-spend-total', '$' + (total.total_usd || 0).toFixed(4));
        setText('kpi-spend-prompt', (total.total_prompt_tokens || 0).toLocaleString());
        setText('kpi-spend-completion', (total.total_completion_tokens || 0).toLocaleString());

        renderBreakdown('analytics-by-model', 'Spend by Model', byModel.breakdown || [], 'model');
        renderBreakdown('analytics-by-provider', 'Spend by Provider', byProvider.breakdown || [], 'provider');
        renderEfficiency(efficiency);
    } catch (e) {
        console.error('Failed to load analytics:', e);
        _analyticsFallback('analytics-by-model', 'Spend by Model');
        _analyticsFallback('analytics-by-provider', 'Spend by Provider');
        _analyticsFallback('analytics-efficiency', 'Cost Efficiency');
    }
}

function _analyticsFallback(id, title) {
    const el = document.getElementById(id);
    if (!el) return;
    el.innerHTML = `
        <h3 class="text-xs font-bold text-white mb-2">${title}</h3>
        <p class="text-[10px] text-rose-400/80 font-mono" role="alert">Unavailable — analytics backend unreachable.</p>
    `;
}

async function _fetchEfficiency() {
    try {
        return await api.fetchCostEfficiency();
    } catch {
        return null;
    }
}

function renderEfficiency(data) {
    const container = document.getElementById('analytics-efficiency');
    if (!container || !data) return;

    const models = data.models || [];
    if (!models.length) {
        container.innerHTML = `
            <h3 class="text-xs font-bold text-white mb-4">Cost Efficiency</h3>
            <p class="text-[9px] text-slate-600 text-center py-4">No efficiency data yet.</p>`;
        return;
    }

    const cheapest = data.cheapest_model || '--';
    const mostExpensive = data.most_expensive_model || '--';

    container.innerHTML = `
        <div class="flex items-center justify-between mb-4">
            <h3 class="text-xs font-bold text-white">Cost Efficiency</h3>
            <div class="flex items-center gap-4">
                <span class="text-[9px] font-mono text-emerald-400">Cheapest: ${cheapest}</span>
                <span class="text-[9px] font-mono text-rose-400">Most expensive: ${mostExpensive}</span>
                <button type="button" id="analytics-efficiency-export" class="text-[9px] font-bold text-slate-400 hover:text-white px-2 py-1 rounded border border-white/10 hover:bg-white/5 transition-colors">Export CSV</button>
            </div>
        </div>
        <div class="overflow-x-auto rounded-xl">
            <table class="w-full min-w-[640px]">
                <thead>
                    <tr class="border-b border-white/[0.08]">
                        <th class="text-left text-[9px] font-bold text-slate-500 uppercase px-3 py-2">Model</th>
                        <th class="text-right text-[9px] font-bold text-slate-500 uppercase px-3 py-2">Requests</th>
                        <th class="text-right text-[9px] font-bold text-slate-500 uppercase px-3 py-2">Total Cost</th>
                        <th class="text-right text-[9px] font-bold text-slate-500 uppercase px-3 py-2">Avg Cost/Req</th>
                        <th class="text-right text-[9px] font-bold text-slate-500 uppercase px-3 py-2">Avg Tokens/Req</th>
                    </tr>
                </thead>
                <tbody>
                    ${models
                        .map(
                            (m) => `
                        <tr class="border-b border-white/[0.04] hover:bg-white/[0.02]">
                            <td class="px-3 py-2 text-[10px] font-mono font-bold text-white">${m.model}</td>
                            <td class="px-3 py-2 text-right text-[10px] font-mono text-sky-400">${(m.requests || 0).toLocaleString()}</td>
                            <td class="px-3 py-2 text-right text-[10px] font-mono text-amber-400">$${(m.total_cost_usd || 0).toFixed(4)}</td>
                            <td class="px-3 py-2 text-right text-[10px] font-mono text-emerald-400">$${(m.avg_cost_per_request_usd || 0).toFixed(6)}</td>
                            <td class="px-3 py-2 text-right text-[10px] font-mono text-slate-400">${Math.round(m.avg_tokens_per_request || 0)}</td>
                        </tr>
                    `
                        )
                        .join('')}
                </tbody>
            </table>
        </div>
        <div class="mt-3 text-[9px] text-slate-600 font-mono">Period total: $${(data.period_total_usd?.total_usd || 0).toFixed(4)}</div>`;

    document.getElementById('analytics-efficiency-export')?.addEventListener('click', () => {
        const exportRows = models.map((m) => ({
            model: m.model,
            requests: m.requests || 0,
            total_cost_usd: m.total_cost_usd || 0,
            avg_cost_per_request_usd: m.avg_cost_per_request_usd || 0,
            avg_tokens_per_request: Math.round(m.avg_tokens_per_request || 0),
        }));
        downloadText(
            `llmproxy-cost-efficiency-${stamp()}.csv`,
            rowsToCsv(
                ['model', 'requests', 'total_cost_usd', 'avg_cost_per_request_usd', 'avg_tokens_per_request'],
                exportRows
            ),
            'text/csv'
        );
    });
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
        <div class="flex items-center justify-between mb-4">
            <h3 class="text-xs font-bold text-white">${title}</h3>
            <button type="button" data-analytics-export="${groupCol}" class="text-[9px] font-bold text-slate-400 hover:text-white px-2 py-1 rounded border border-white/10 hover:bg-white/5 transition-colors">Export CSV</button>
        </div>
        <div class="overflow-x-auto rounded-xl">
            <table class="w-full min-w-[640px]">
                <thead>
                    <tr class="border-b border-white/[0.08]">
                        <th class="text-left text-[9px] font-bold text-slate-500 uppercase tracking-widest px-3 py-2">${groupCol}</th>
                        <th class="text-right text-[9px] font-bold text-slate-500 uppercase tracking-widest px-3 py-2">Requests</th>
                        <th class="text-right text-[9px] font-bold text-slate-500 uppercase tracking-widest px-3 py-2">Cost</th>
                        <th class="text-right text-[9px] font-bold text-slate-500 uppercase tracking-widest px-3 py-2">Avg Latency</th>
                    </tr>
                </thead>
                <tbody>
                    ${rows
                        .map((r) => {
                            const name = r[groupCol] || 'unknown';
                            const cost = r.total_cost_usd || 0;
                            const costColor =
                                cost > 1 ? 'text-rose-400' : cost > 0.1 ? 'text-amber-400' : 'text-emerald-400';
                            return `
                        <tr class="border-b border-white/[0.04] hover:bg-white/[0.02] transition-colors">
                            <td class="px-3 py-2"><span class="text-[10px] font-bold text-white font-mono">${name}</span></td>
                            <td class="px-3 py-2 text-right"><span class="text-[10px] font-mono text-sky-400">${(r.requests || 0).toLocaleString()}</span></td>
                            <td class="px-3 py-2 text-right"><span class="text-[10px] font-mono ${costColor}">$${cost.toFixed(4)}</span></td>
                            <td class="px-3 py-2 text-right"><span class="text-[10px] font-mono text-slate-400">${(r.avg_latency_ms || 0).toFixed(0)}ms</span></td>
                        </tr>`;
                        })
                        .join('')}
                </tbody>
            </table>
        </div>
    `;

    container.querySelector(`[data-analytics-export="${groupCol}"]`)?.addEventListener('click', () => {
        const exportRows = rows.map((r) => ({
            [groupCol]: r[groupCol] || 'unknown',
            requests: r.requests || 0,
            total_cost_usd: r.total_cost_usd || 0,
            avg_latency_ms: r.avg_latency_ms || 0,
        }));
        downloadText(
            `llmproxy-spend-by-${groupCol}-${stamp()}.csv`,
            rowsToCsv([groupCol, 'requests', 'total_cost_usd', 'avg_latency_ms'], exportRows),
            'text/csv'
        );
    });
}

function setText(id, value) {
    const el = document.getElementById(id);
    if (el) {
        el.textContent = value;
        el.classList.remove('skeleton');
    }
}
