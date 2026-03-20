/**
 * Threats View — Security dashboard with KPI cards, budget gauge, threat timeline, event feed.
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
        const text = await api.fetchMetrics();

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

        // Extended metrics
        setText('kpi-errors', errors.toLocaleString());
        setText('kpi-tokens', tokens > 1000 ? (tokens / 1000).toFixed(1) + 'k' : tokens.toLocaleString());

        // Budget gauge
        renderBudgetGauge(budgetConsumed, budgetLimit, cost);

    } catch {
        // Backend unavailable
    }
}

function setText(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
}

function renderBudgetGauge(consumed, limit, totalCost) {
    const container = document.getElementById('budget-gauge');
    if (!container) return;

    if (limit <= 0 && totalCost <= 0) {
        container.innerHTML = `
            <div class="flex items-center gap-3">
                <div class="text-[9px] text-slate-600 font-mono">No budget configured</div>
            </div>`;
        return;
    }

    const pct = limit > 0 ? Math.min((consumed / limit) * 100, 100) : 0;
    const color = pct > 80 ? 'rose' : pct > 50 ? 'amber' : 'emerald';
    const remaining = limit > 0 ? (limit - consumed).toFixed(2) : '--';

    container.innerHTML = `
        <div class="flex items-center justify-between mb-2">
            <div>
                <span class="text-lg font-black font-mono text-white">$${consumed.toFixed(2)}</span>
                ${limit > 0 ? `<span class="text-[10px] text-slate-500"> / $${limit.toFixed(2)}</span>` : ''}
            </div>
            <span class="text-[9px] font-mono text-${color}-400">${limit > 0 ? pct.toFixed(0) + '% used' : '$' + totalCost.toFixed(4) + ' total'}</span>
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
                x: {
                    stacked: true,
                    ticks: { color: '#334155', font: { size: 9 } },
                    grid: { color: 'rgba(255,255,255,0.03)' },
                },
                y: {
                    stacked: true,
                    ticks: { color: '#334155', font: { size: 9 } },
                    grid: { color: 'rgba(255,255,255,0.03)' },
                },
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

    while (feed.children.length > 50) {
        feed.removeChild(feed.lastChild);
    }
}

export function renderThreats() {
    // KPIs are updated via refreshMetrics interval
}
