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
    refreshLatencyData();
    store.poll(refreshMetrics, 10000, 'threats');
    store.poll(refreshLatencyData, 10000, 'threats');
}

async function refreshMetrics() {
    try {
        const [text, guardsStatus, health] = await Promise.all([
            api.fetchMetrics().catch(() => ''),
            api.fetchGuardsStatus().catch(() => null),
            api.fetchHealth().catch(() => null),
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

        // Health / uptime
        if (health) {
            const uptime = health.uptime_seconds || 0;
            const h = Math.floor(uptime / 3600);
            const m = Math.floor((uptime % 3600) / 60);
            setText('kpi-uptime', h > 0 ? `${h}h ${m}m` : `${m}m`);
            const poolSize = health.pool_size || 0;
            const poolHealthy = health.pool_healthy || 0;
            setText('kpi-pool-health', `${poolHealthy}/${poolSize}`);
        }

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

    // Use guardsStatus for more accurate budget data (includes daily_limit from config)
    if (guardsStatus?.budget) {
        consumed = guardsStatus.budget.total_cost_today || consumed;
        if (limit <= 0 && guardsStatus.budget.daily_limit > 0) {
            limit = guardsStatus.budget.daily_limit;
        }
    }

    // Always render the gauge if we have a configured limit
    if (limit <= 0 && totalCost <= 0 && consumed <= 0) {
        container.innerHTML = `<div class="text-[9px] text-slate-600 font-mono">No budget configured — set <code class="text-slate-500">budget.daily_limit</code> in config.yaml</div>`;
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
                <span class="text-[10px] text-slate-600 font-mono">$${remaining} remaining</span>
                <span class="text-[10px] text-slate-600 font-mono">Daily reset</span>
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
                        <span class="text-[10px] font-mono text-slate-500 truncate max-w-[250px]">${sig}</span>
                        <span class="text-[10px] font-mono text-rose-400">${count}x</span>
                    </div>
                `).join('')}
            </div>
        ` : ''}
    `;
}

const RING_COLORS = {
    ingress: { bar: 'bg-rose-500/60', text: 'text-rose-400', label: 'INGRESS' },
    pre_flight: { bar: 'bg-amber-500/60', text: 'text-amber-400', label: 'PRE-FLIGHT' },
    routing: { bar: 'bg-sky-500/60', text: 'text-sky-400', label: 'ROUTING' },
    post_flight: { bar: 'bg-violet-500/60', text: 'text-violet-400', label: 'POST-FLIGHT' },
    background: { bar: 'bg-teal-500/60', text: 'text-teal-400', label: 'BACKGROUND' },
};

async function refreshLatencyData() {
    try {
        const [latency, timeline] = await Promise.all([
            api.fetchLatencyMetrics().catch(() => null),
            api.fetchRingTimeline().catch(() => null),
        ]);
        if (latency) {
            renderRingLatencyBars(latency);
            renderTTFT(latency.ttft);
        }
        if (timeline) renderRingTimeline(timeline.traces || []);
    } catch {}
}

function renderRingLatencyBars(latency) {
    const container = document.getElementById('ring-latency-bars');
    if (!container) return;

    const rings = latency.rings || {};
    const ringNames = Object.keys(RING_COLORS);
    const maxP99 = Math.max(1, ...ringNames.map(r => (rings[r]?.p99 || 0)));

    if (ringNames.every(r => !rings[r]?.count)) {
        container.innerHTML = `<p class="text-[9px] text-slate-600 font-mono">Collecting samples...</p>`;
        return;
    }

    container.innerHTML = ringNames.map(ring => {
        const r = rings[ring] || { p50: 0, p95: 0, p99: 0, count: 0 };
        const rc = RING_COLORS[ring];
        const barWidth = maxP99 > 0 ? Math.max(2, (r.p99 / maxP99) * 100) : 0;
        return `
            <div class="mb-2">
                <div class="flex items-center justify-between mb-0.5">
                    <span class="text-[10px] font-bold ${rc.text} uppercase tracking-wider">${rc.label}</span>
                    <div class="flex items-center gap-3">
                        <span class="text-[10px] font-mono text-slate-500">P50 <span class="text-white">${r.p50.toFixed(1)}ms</span></span>
                        <span class="text-[10px] font-mono text-slate-500">P95 <span class="text-amber-400">${r.p95.toFixed(1)}ms</span></span>
                        <span class="text-[10px] font-mono text-slate-500">P99 <span class="text-rose-400">${r.p99.toFixed(1)}ms</span></span>
                        <span class="text-[9px] font-mono text-slate-600">${r.count}x</span>
                    </div>
                </div>
                <div class="w-full h-1.5 bg-white/5 rounded-full overflow-hidden">
                    <div class="h-full ${rc.bar} rounded-full transition-all" style="width: ${barWidth}%"></div>
                </div>
            </div>
        `;
    }).join('');
}

function renderTTFT(ttft) {
    const container = document.getElementById('ttft-metrics');
    if (!container) return;

    if (!ttft || ttft.samples === 0) {
        container.innerHTML = `<p class="text-[9px] text-slate-600 font-mono">No streaming data yet</p>`;
        return;
    }

    const color = ttft.p95 > 1000 ? 'rose' : ttft.p95 > 500 ? 'amber' : 'emerald';
    container.innerHTML = `
        <div class="flex items-center gap-6 mb-3">
            <div>
                <span class="text-2xl font-black font-mono text-white">${ttft.p50.toFixed(0)}</span>
                <span class="text-[10px] text-slate-500 ml-1">ms P50</span>
            </div>
            <div>
                <span class="text-lg font-black font-mono text-${color}-400">${ttft.p95.toFixed(0)}</span>
                <span class="text-[10px] text-slate-500 ml-1">ms P95</span>
            </div>
            <div>
                <span class="text-lg font-black font-mono text-rose-400">${ttft.p99.toFixed(0)}</span>
                <span class="text-[10px] text-slate-500 ml-1">ms P99</span>
            </div>
        </div>
        <div class="flex items-center gap-2">
            <span class="text-[10px] font-mono text-slate-600">${ttft.samples} stream samples</span>
            <div class="flex-1 h-1 bg-white/5 rounded-full overflow-hidden">
                <div class="h-full bg-${color}-500/40 rounded-full" style="width: ${Math.min(100, (ttft.p50 / 2000) * 100)}%"></div>
            </div>
            <span class="text-[10px] font-mono text-slate-600">2s target</span>
        </div>
    `;
}

function renderRingTimeline(traces) {
    const container = document.getElementById('ring-timeline');
    if (!container) return;

    if (!traces.length) {
        container.innerHTML = `<p class="text-[9px] text-slate-600 font-mono">No request traces yet</p>`;
        return;
    }

    container.innerHTML = traces.map(trace => {
        const rings = trace.rings || {};
        const total = trace.total_ms || 0;
        const upstream = trace.upstream_ms || 0;
        const ringNames = ['ingress', 'pre_flight', 'routing', 'post_flight', 'background'];
        const maxMs = Math.max(1, total || Object.values(rings).reduce((s, r) => s + (r.duration_ms || 0), 0) + upstream);

        const segments = ringNames.map(ring => {
            const r = rings[ring];
            if (!r) return '';
            const rc = RING_COLORS[ring];
            const width = Math.max(1, (r.duration_ms / maxMs) * 100);
            const plugins = (r.plugins || []).map(p => `${p.name}: ${p.ms}ms`).join(', ');
            return `<div class="${rc.bar} h-full rounded-sm" style="width: ${width}%" title="${rc.label}: ${r.duration_ms}ms\n${plugins}"></div>`;
        }).join('');

        const upstreamWidth = upstream > 0 ? Math.max(1, (upstream / maxMs) * 100) : 0;
        const upstreamSeg = upstreamWidth > 0 ? `<div class="bg-emerald-500/60 h-full rounded-sm" style="width: ${upstreamWidth}%" title="Upstream: ${upstream}ms"></div>` : '';

        const ts = trace.timestamp ? new Date(trace.timestamp * 1000).toLocaleTimeString() : '--';
        const ttftBadge = trace.ttft_ms ? `<span class="text-[9px] font-mono text-sky-400 bg-sky-500/10 px-1 py-0.5 rounded">TTFT ${trace.ttft_ms}ms</span>` : '';

        return `
            <div class="flex items-center gap-3 group">
                <span class="text-[10px] font-mono text-slate-600 w-16 shrink-0">${ts}</span>
                <span class="text-[9px] font-mono text-slate-500 w-12 shrink-0">${trace.req_id || '--'}</span>
                <div class="flex-1 h-3 bg-white/[0.03] rounded-full overflow-hidden flex">
                    ${segments}${upstreamSeg}
                </div>
                <span class="text-[10px] font-mono text-white w-16 text-right shrink-0">${total.toFixed(0)}ms</span>
                ${ttftBadge}
            </div>
        `;
    }).join('');
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

    let errorCount = 0;

    function connect() {
        try {
            const _token = localStorage.getItem('proxy_key') || '';
            if (!_token) {
                // Defer until user has logged in
                setTimeout(connect, 2000);
                return;
            }
            if (eventSource) eventSource.close();
            errorCount = 0;
            eventSource = new EventSource(`${window.location.origin}/api/v1/logs?token=${encodeURIComponent(_token)}`);
            eventSource.onmessage = (e) => {
                errorCount = 0;
                try {
                    const entry = JSON.parse(e.data);
                    if (!isSecurityEvent(entry)) return;
                    addEventToFeed(feed, entry);
                } catch {}
            };
            eventSource.onerror = () => {
                errorCount++;
                if (errorCount > 5) {
                    eventSource.close();
                    feed.innerHTML = `<div class="flex items-center gap-2">
                        <p class="text-[10px] text-slate-500">Event stream disconnected.</p>
                        <button id="sse-reconnect-btn" class="text-[10px] text-sky-400 hover:text-sky-300 font-bold">Reconnect</button>
                    </div>`;
                    const btn = document.getElementById('sse-reconnect-btn');
                    if (btn) btn.addEventListener('click', connect);
                }
            };
        } catch {
            feed.innerHTML = '<p class="text-[10px] text-slate-500 italic">Event stream unavailable.</p>';
        }
    }

    connect();
}

function isSecurityEvent(entry) {
    const level = (entry.level || '').toUpperCase();
    const msg = (entry.message || '').toUpperCase();
    return level === 'SECURITY' || level === 'WARNING' || level === 'ERROR' || level === 'CRITICAL' ||
        msg.includes('SHIELD') || msg.includes('BLOCK') || msg.includes('INJECT') ||
        msg.includes('PII') || msg.includes('FIREWALL') || msg.includes('AUTH') ||
        msg.includes('RATE') || msg.includes('ZT') || msg.includes('PANIC') || msg.includes('BUDGET');
}

function updateChart(entry) {
    if (!chart) return;
    const hour = new Date().getHours();
    const isBlocked = (entry.level || '').toUpperCase() === 'SECURITY' ||
        (entry.message || '').toUpperCase().includes('BLOCK');
    if (isBlocked) {
        chart.data.datasets[0].data[hour] += 1;
    } else {
        chart.data.datasets[1].data[hour] += 1;
    }
    chart.update('none'); // skip animation for perf
}

function addEventToFeed(feed, entry) {
    const placeholder = feed.querySelector('p.italic');
    if (placeholder) placeholder.remove();
    updateChart(entry);

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
