/**
 * Threats View — Security dashboard with KPI cards, budget gauge, per-endpoint
 * breakdown, latency percentiles, threat timeline, and live event feed.
 */
import { store } from '../services/store.js';
import { api } from '../services/api.js';

let chart = null;
let eventSource = null;

export function initThreats() {
    initChart();
    initEventFeed();
    refreshMetrics();
    setInterval(refreshMetrics, 10000);
}

async function refreshMetrics() {
    try {
        const [text, guardsStatus] = await Promise.all([
            api.fetchMetrics().catch(() => ''),
            api.fetchGuardsStatus().catch(() => null),
        ]);

        const requests = extractMetric(text, 'llm_proxy_requests_total') || 0;
        const blocked = extractMetric(text, 'llm_proxy_injection_blocked_total') || 0;
        const authFails = extractMetric(text, 'llm_proxy_auth_failures_total') || 0;
        const totalBlocked = blocked + authFails;
        const passRate = requests > 0 ? ((1 - totalBlocked / requests) * 100).toFixed(1) + '%' : '100%';
        const errors = extractMetric(text, 'llm_proxy_request_errors_total') || 0;
        const tokens = extractMetric(text, 'llm_proxy_token_usage_total') || 0;
        const cost = extractMetric(text, 'llm_proxy_cost_total') || 0;
        const budgetConsumed = extractMetric(text, 'llm_proxy_budget_consumed_usd') || 0;
        const budgetLimit = extractMetric(text, 'llm_proxy_budget_limit_usd') || 0;

        // KPI cards
        setText('kpi-requests', requests.toLocaleString());
        setText('kpi-blocked', totalBlocked.toLocaleString());
        setText('kpi-pii', blocked > 0 ? blocked.toLocaleString() : '0');
        setText('kpi-passrate', passRate);
        setText('kpi-errors', errors.toLocaleString());
        setText('kpi-tokens', tokens > 1000 ? (tokens / 1000).toFixed(1) + 'k' : tokens.toLocaleString());

        // Budget gauge
        renderBudgetGauge(budgetConsumed, budgetLimit, cost, guardsStatus);

        // Per-endpoint breakdown
        renderEndpointBreakdown(text);

        // Firewall stats
        if (guardsStatus) renderFirewallStats(guardsStatus);

    } catch {
        // Backend unavailable
    }
}

function setText(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
}

function renderBudgetGauge(consumed, limit, totalCost, guardsStatus) {
    const container = document.getElementById('budget-gauge');
    if (!container) return;

    // Use guardsStatus for more accurate budget data
    if (guardsStatus?.budget) {
        consumed = guardsStatus.budget.total_cost_today || consumed;
    }

    if (limit <= 0 && totalCost <= 0 && consumed <= 0) {
        container.innerHTML = `<div class="text-[9px] text-slate-600 font-mono">No budget activity yet</div>`;
        return;
    }

    const pct = limit > 0 ? Math.min((consumed / limit) * 100, 100) : 0;
    const color = pct > 80 ? 'rose' : pct > 50 ? 'amber' : 'emerald';
    const remaining = limit > 0 ? (limit - consumed).toFixed(2) : '--';

    container.innerHTML = `
        <div class="flex items-center justify-between mb-2">
            <div>
                <span class="text-lg font-black font-mono text-white">$${consumed.toFixed(4)}</span>
                ${limit > 0 ? `<span class="text-[10px] text-slate-500"> / $${limit.toFixed(2)}</span>` : ''}
            </div>
            <span class="text-[9px] font-mono text-${color}-400">${limit > 0 ? pct.toFixed(0) + '% used' : 'tracking'}</span>
        </div>
        ${limit > 0 ? `
            <div class="w-full h-2 bg-white/5 rounded-full overflow-hidden">
                <div class="h-full bg-${color}-500/60 rounded-full transition-all" style="width: ${pct}%"></div>
            </div>
            <div class="flex justify-between mt-1">
                <span class="text-[8px] text-slate-600 font-mono">$${remaining} remaining</span>
                <span class="text-[8px] text-slate-600 font-mono">Daily reset</span>
            </div>
        ` : ''}
    `;
}

function renderEndpointBreakdown(text) {
    const container = document.getElementById('endpoint-breakdown');
    if (!container) return;

    // Parse per-endpoint metrics from Prometheus text
    const endpoints = {};
    const lines = text.split('\n');
    for (const line of lines) {
        if (line.startsWith('#')) continue;
        const match = line.match(/llm_proxy_requests_total\{.*endpoint="([^"]+)".*\}\s+([\d.]+)/);
        if (match) {
            const [, ep, val] = match;
            if (!endpoints[ep]) endpoints[ep] = { requests: 0, errors: 0 };
            endpoints[ep].requests += parseFloat(val);
        }
        const errMatch = line.match(/llm_proxy_request_errors_total\{.*endpoint="([^"]+)".*\}\s+([\d.]+)/);
        if (errMatch) {
            const [, ep, val] = errMatch;
            if (!endpoints[ep]) endpoints[ep] = { requests: 0, errors: 0 };
            endpoints[ep].errors += parseFloat(val);
        }
    }

    const entries = Object.entries(endpoints);
    if (entries.length === 0) {
        container.innerHTML = `<p class="text-[9px] text-slate-600 font-mono">No per-endpoint data yet</p>`;
        return;
    }

    container.innerHTML = entries.map(([ep, data]) => {
        const errRate = data.requests > 0 ? ((data.errors / data.requests) * 100).toFixed(1) : '0.0';
        const errColor = parseFloat(errRate) > 5 ? 'text-rose-400' : parseFloat(errRate) > 0 ? 'text-amber-400' : 'text-emerald-400';
        return `
            <div class="flex items-center justify-between py-1.5 border-b border-white/[0.04] last:border-0">
                <span class="text-[9px] font-mono text-slate-400 truncate max-w-[200px]">${ep}</span>
                <div class="flex items-center gap-4">
                    <span class="text-[9px] font-mono text-slate-500">${data.requests.toLocaleString()} req</span>
                    <span class="text-[9px] font-mono ${errColor}">${errRate}% err</span>
                </div>
            </div>
        `;
    }).join('');
}

function renderFirewallStats(guardsStatus) {
    const container = document.getElementById('firewall-stats');
    if (!container) return;

    const fw = guardsStatus.firewall || {};
    const scanned = fw.total_scanned || 0;
    const fwBlocked = fw.total_blocked || 0;
    const signatures = fw.block_by_signature || {};
    const sigEntries = Object.entries(signatures);

    container.innerHTML = `
        <div class="flex items-center gap-6 mb-2">
            <div>
                <span class="text-lg font-black font-mono text-white">${scanned.toLocaleString()}</span>
                <span class="text-[9px] text-slate-500 ml-1">scanned</span>
            </div>
            <div>
                <span class="text-lg font-black font-mono ${fwBlocked > 0 ? 'text-rose-400' : 'text-emerald-400'}">${fwBlocked}</span>
                <span class="text-[9px] text-slate-500 ml-1">blocked</span>
            </div>
        </div>
        ${sigEntries.length > 0 ? `
            <div class="space-y-1 mt-2 pt-2 border-t border-white/[0.04]">
                ${sigEntries.map(([sig, count]) => `
                    <div class="flex items-center justify-between">
                        <span class="text-[8px] font-mono text-slate-500 truncate max-w-[250px]">${sig}</span>
                        <span class="text-[8px] font-mono text-rose-400">${count}x</span>
                    </div>
                `).join('')}
            </div>
        ` : ''}
    `;
}

function extractMetric(text, name) {
    const lines = text.split('\n');
    let total = 0;
    for (const line of lines) {
        if (line.startsWith(name) && !line.startsWith('#')) {
            const val = parseFloat(line.split(' ').pop());
            if (!isNaN(val)) total += val;
        }
    }
    return total;
}

function initChart() {
    const canvas = document.getElementById('threat-chart');
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    const labels = Array.from({ length: 24 }, (_, i) => `${i}:00`);
    const blocked = Array(24).fill(0);
    const passed = Array(24).fill(0);

    chart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels,
            datasets: [
                {
                    label: 'Blocked',
                    data: blocked,
                    backgroundColor: 'rgba(244, 63, 94, 0.4)',
                    borderColor: 'rgba(244, 63, 94, 0.8)',
                    borderWidth: 1,
                    borderRadius: 4,
                },
                {
                    label: 'Passed',
                    data: passed,
                    backgroundColor: 'rgba(52, 211, 153, 0.2)',
                    borderColor: 'rgba(52, 211, 153, 0.5)',
                    borderWidth: 1,
                    borderRadius: 4,
                },
            ],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    labels: { color: '#64748b', font: { size: 10, family: 'JetBrains Mono' } },
                },
            },
            scales: {
                x: { stacked: true, ticks: { color: '#334155', font: { size: 9 } }, grid: { color: 'rgba(255,255,255,0.03)' } },
                y: { stacked: true, ticks: { color: '#334155', font: { size: 9 } }, grid: { color: 'rgba(255,255,255,0.03)' } },
            },
        },
    });
}

function initEventFeed() {
    const feed = document.getElementById('threat-feed');
    if (!feed) return;

    try {
        eventSource = new EventSource(`${window.location.origin}/api/v1/logs`);
        eventSource.onmessage = (e) => {
            try {
                const entry = JSON.parse(e.data);
                if (!isSecurityEvent(entry)) return;
                addEventToFeed(feed, entry);
            } catch {}
        };
        eventSource.onerror = () => {
            feed.innerHTML = '<p class="text-[10px] text-slate-500 italic">Event stream disconnected. Reconnecting...</p>';
        };
    } catch {
        feed.innerHTML = '<p class="text-[10px] text-slate-500 italic">Event stream unavailable.</p>';
    }
}

function isSecurityEvent(entry) {
    const level = (entry.level || '').toUpperCase();
    const msg = (entry.message || '').toUpperCase();
    return level === 'SECURITY' || level === 'WARNING' || level === 'ERROR' || level === 'CRITICAL' ||
        msg.includes('SHIELD') || msg.includes('BLOCK') || msg.includes('INJECT') ||
        msg.includes('PII') || msg.includes('FIREWALL') || msg.includes('AUTH') ||
        msg.includes('RATE') || msg.includes('ZT') || msg.includes('PANIC') || msg.includes('BUDGET');
}

function addEventToFeed(feed, entry) {
    const placeholder = feed.querySelector('p.italic');
    if (placeholder) placeholder.remove();

    const level = (entry.level || 'INFO').toUpperCase();
    const colors = {
        CRITICAL: 'text-red-300 bg-red-500/20 border-red-500/30',
        SECURITY: 'text-rose-400 bg-rose-500/10 border-rose-500/20',
        WARNING: 'text-amber-400 bg-amber-500/10 border-amber-500/20',
        ERROR: 'text-red-400 bg-red-500/10 border-red-500/20',
        INFO: 'text-sky-400 bg-sky-500/10 border-sky-500/20',
    };
    const color = colors[level] || colors.INFO;

    const el = document.createElement('div');
    el.className = `flex items-start gap-3 p-3 rounded-xl border ${color} transition-all`;
    el.innerHTML = `
        <span class="text-[9px] font-mono text-slate-500 shrink-0 mt-0.5">${entry.timestamp || '--:--'}</span>
        <span class="text-[9px] font-black uppercase w-16 shrink-0 mt-0.5">${level}</span>
        <span class="text-[10px] font-mono flex-1">${entry.message || ''}</span>
    `;

    feed.insertBefore(el, feed.firstChild);
    while (feed.children.length > 50) feed.removeChild(feed.lastChild);
}

export function renderThreats() {}
