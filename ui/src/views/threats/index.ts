/**
 * Threats view orchestrator. Imported from `components/threats.js` (the
 * existing JS shell) and called after `initThreats()` so the new TS-rendered
 * KPI grid, event feed, and sub-sections take over their DOM mount points.
 *
 * Phase G.5 closed the strangler-fig loop: every Threats sub-section
 * (budget gauge, firewall stats, per-endpoint breakdown, ring latency,
 * TTFT, ring timeline, threat chart) now mounts here.
 */
import { renderThreatKpis } from './Kpi';
import { ThreatEventFeed } from './EventFeed';
import { buildKpiData, extractMetric } from './parseMetrics';
import type { ThreatsKpiData } from './types';
import { renderBudgetGauge } from './sections/BudgetGauge';
import { renderEndpointBreakdown } from './sections/EndpointBreakdown';
import { renderFirewallStats } from './sections/FirewallStats';
import { renderRingLatencyBars, renderTtft } from './sections/RingLatency';
import { renderRingTimeline } from './sections/RingTimeline';
import {
    renderSpendForecast,
    type SpendForecastApi,
    type SpendForecastBlock,
} from './sections/SpendForecast';
import { mountThreatChart, type ThreatChartHandle } from './sections/ThreatChart';
import { renderTrafficFlow, type FlowData, type FlowNode } from './sections/TrafficFlow';
import type { GuardsStatus, LatencyMetrics, TimelinePayload } from './sections/types';

interface ThreatsApi {
    fetchMetrics: () => Promise<string>;
    fetchHealth: () => Promise<{ uptime_seconds?: number; pool_size?: number; pool_healthy?: number } | null>;
    /**
     * Q.3 — optional hourly-bucket time series for KPI sparklines. Optional
     * so older shells without the route still work; an absent / failing
     * fetch just means tiles render without sparklines.
     */
    fetchHourlyBuckets?: () => Promise<{ series?: Record<string, number[]> } | null>;
}

interface MountOptions {
    api: ThreatsApi;
    poll?: (fn: () => void, intervalMs: number) => () => void;
    pollIntervalMs?: number;
}

/** Mount the KPI grid + start its polling. Returns a cleanup function. */
export function mountThreatsKpis(container: HTMLElement, opts: MountOptions): () => void {
    let lastData: ThreatsKpiData | null = null;
    let lastError: string | undefined;
    let lastSeries: Record<string, number[]> = {};

    renderThreatKpis(container, null);

    const refresh = async () => {
        try {
            // Q.3 — fetch hourly buckets in parallel with the existing
            // metrics + health pull. Buckets are optional; absent or failed
            // → tiles render without sparklines, no error surfaced.
            const bucketsFetch = opts.api.fetchHourlyBuckets
                ? opts.api.fetchHourlyBuckets().catch(() => null)
                : Promise.resolve(null);

            const [text, health, buckets] = await Promise.all([
                opts.api.fetchMetrics().catch(() => ''),
                opts.api.fetchHealth().catch(() => null),
                bucketsFetch,
            ]);
            lastData = buildKpiData(text, health);
            lastError = undefined;
            if (buckets && buckets.series) lastSeries = buckets.series;
            renderThreatKpis(container, lastData, undefined, lastSeries);
        } catch (err) {
            lastError = (err as Error)?.message || 'Backend unreachable';
            // Keep the last good data on screen if we have any; otherwise show errors.
            renderThreatKpis(container, lastData, lastData ? undefined : lastError, lastSeries);
        }
    };

    void refresh();
    const stopper = opts.poll
        ? opts.poll(refresh, opts.pollIntervalMs ?? 10_000)
        : (() => {
              const id = setInterval(refresh, opts.pollIntervalMs ?? 10_000);
              return () => clearInterval(id);
          })();
    return stopper;
}

/** Mount the live event feed. Returns the feed instance for tests + manual control. */
export function mountThreatsEventFeed(container: HTMLElement): ThreatEventFeed {
    const feed = new ThreatEventFeed(container);
    feed.connect();
    return feed;
}

export interface SectionsApi extends SpendForecastApi {
    fetchMetrics: () => Promise<string>;
    fetchGuardsStatus: () => Promise<GuardsStatus | null>;
    fetchLatencyMetrics: () => Promise<LatencyMetrics | null>;
    fetchRingTimeline: () => Promise<TimelinePayload | null>;
}

export interface SectionsHosts {
    budget: HTMLElement | null;
    firewall: HTMLElement | null;
    breakdown: HTMLElement | null;
    ringLatency: HTMLElement | null;
    ttft: HTMLElement | null;
    ringTimeline: HTMLElement | null;
    chartCanvas: HTMLCanvasElement | null;
    trafficFlow?: HTMLElement | null;
    /** P.2 — Spend forecast tile grid (time to limit / burn rate / projected). */
    spendForecast?: HTMLElement | null;
}

export interface SectionsOptions {
    api: SectionsApi;
    poll?: (fn: () => void, intervalMs: number) => () => void;
    pollIntervalMs?: number;
    /** Bridges firewall live status into a global store. Optional. */
    onFirewallState?: (state: { enabled: boolean; disabled_reason: string | null }) => void;
}

/**
 * Mount the budget gauge, firewall stats, endpoint breakdown, ring latency
 * bars, TTFT card, ring timeline, and the 24h threat chart. Each section
 * has its own resilient render: a 503 on /api/v1/metrics/latency does not
 * blank out the budget gauge.
 */
export function mountThreatsSections(hosts: SectionsHosts, opts: SectionsOptions): () => void {
    let chart: ThreatChartHandle | null = null;
    if (hosts.chartCanvas) {
        chart = mountThreatChart(hosts.chartCanvas);
    }

    // Initial loading state for the spend forecast — renders skeleton tiles
    // immediately so we don't get a layout shift when the first poll lands.
    if (hosts.spendForecast) renderSpendForecast(hosts.spendForecast, null);

    const refresh = async (): Promise<void> => {
        const [promText, guards, latency, timeline, forecast] = await Promise.all([
            opts.api.fetchMetrics().catch(() => ''),
            opts.api.fetchGuardsStatus().catch(() => null),
            opts.api.fetchLatencyMetrics().catch(() => null),
            opts.api.fetchRingTimeline().catch(() => null),
            opts.api.fetchSpendForecast().catch((err) => ({ __error: (err as Error)?.message ?? 'unreachable' } as unknown as SpendForecastBlock)),
        ]);

        // Budget + firewall are powered by /metrics + guards-status combined.
        if (hosts.budget) {
            const cost = extractMetric(promText, 'llm_proxy_cost_total');
            const consumed = extractMetric(promText, 'llm_proxy_budget_consumed_usd');
            const limit = extractMetric(promText, 'llm_proxy_budget_limit_usd');
            renderBudgetGauge(hosts.budget, { cost, consumed, limit, guardsStatus: guards });
        }
        if (hosts.firewall && guards) {
            renderFirewallStats(hosts.firewall, guards);
        }
        if (guards?.firewall) {
            opts.onFirewallState?.({
                enabled: guards.firewall.enabled !== false,
                disabled_reason: guards.firewall.disabled_reason ?? null,
            });
        }
        if (hosts.breakdown) {
            renderEndpointBreakdown(hosts.breakdown, promText);
        }
        if (latency) {
            if (hosts.ringLatency) renderRingLatencyBars(hosts.ringLatency, latency);
            if (hosts.ttft) renderTtft(hosts.ttft, latency.ttft);
        }
        if (hosts.ringTimeline && timeline) {
            renderRingTimeline(hosts.ringTimeline, timeline.traces ?? []);
        }
        // O.4 — TrafficFlow uses /metrics + guards-status + the live registry
        // already in the store. Build a logical view of "Clients → Guards →
        // Router → Providers" with current state per node.
        if (hosts.trafficFlow) {
            renderTrafficFlow(hosts.trafficFlow, _buildFlowData(promText, guards));
        }
        // P.2 — Spend forecast tiles. Errors from /forecast are surfaced on
        // the leading tile only, the rest stays renderable (operator still
        // sees burn rate from /metrics if forecast is down).
        if (hosts.spendForecast) {
            const sentinel = forecast as unknown as { __error?: string };
            if (sentinel.__error) {
                renderSpendForecast(hosts.spendForecast, null, sentinel.__error);
            } else {
                renderSpendForecast(hosts.spendForecast, forecast);
            }
        }
    };

    void refresh();
    const stop = opts.poll
        ? opts.poll(refresh, opts.pollIntervalMs ?? 10_000)
        : (() => {
              const id = setInterval(refresh, opts.pollIntervalMs ?? 10_000);
              return () => clearInterval(id);
          })();

    return () => {
        stop();
        chart?.destroy();
    };
}

// ── O.4 — Build the FlowData shape from the polled inputs the section
// already has. The view is logical, not a true Sankey: each node carries
// state (live / idle / blocked / down) so the SVG can color + pulse;
// we don't yet have per-edge token counts (queued for backend hourly
// buckets). The point now is "operator sees the pipeline and what's
// active".
function _buildFlowData(promText: string, guards: GuardsStatus | null): FlowData {
    const reqs = extractMetric(promText, 'llm_proxy_requests_total');
    const blocked = extractMetric(promText, 'llm_proxy_injection_blocked_total');

    // Guards — surface the four canonical ones we always show in the Guards
    // tab. Each can be enabled / disabled / blocking.
    const features = guards?.features ?? {};
    const blockBySig = guards?.firewall?.block_by_signature ?? {};
    const guardNodes: FlowNode[] = [
        {
            id: 'firewall',
            label: 'Firewall',
            sub: guards?.firewall?.enabled !== false ? 'WAF · L1' : 'OFF',
            state:
                guards?.firewall?.enabled === false
                    ? 'down'
                    : Object.keys(blockBySig).length > 0
                      ? 'blocked'
                      : 'live',
        },
        {
            id: 'injection_guard',
            label: 'Injection',
            sub: features.injection_guard !== false ? 'L2' : 'OFF',
            state: features.injection_guard !== false ? 'live' : 'idle',
        },
        {
            id: 'pii_masker',
            label: 'PII Mask',
            sub: 'L2',
            state: 'live',
        },
        {
            id: 'link_sanitizer',
            label: 'Link Scrub',
            sub: features.link_sanitizer !== false ? 'L4' : 'OFF',
            state: features.link_sanitizer !== false ? 'live' : 'idle',
        },
    ];

    // Providers — derive from circuit_breakers. Each entry maps to one
    // provider node with live (closed circuit), blocked (open), or down state.
    const cbs = guards?.circuit_breakers ?? {};
    const providerNodes: FlowNode[] = Object.keys(cbs).map((id) => {
        const state = (cbs[id]?.state ?? 'closed').toLowerCase();
        return {
            id,
            label: id.length > 14 ? id.slice(0, 12) + '…' : id,
            sub: state === 'closed' ? 'LIVE' : state.toUpperCase(),
            state: state === 'open' ? 'down' : state === 'half_open' ? 'blocked' : 'live',
        };
    });
    if (providerNodes.length === 0) {
        // Onboarding state — no endpoints registered yet.
        providerNodes.push({ id: 'none', label: 'No endpoints', sub: 'add one', state: 'idle' });
    }

    return {
        clientsLabel: reqs > 0 ? reqs.toLocaleString() : '—',
        clientsSub: reqs > 0 ? `req · ${blocked > 0 ? `${blocked} blk` : 'no blk'}` : 'no traffic yet',
        guards: guardNodes,
        router: {
            id: 'router',
            label: 'Router',
            sub: 'smart',
            state: 'live',
        },
        providers: providerNodes,
    };
}

export { ThreatEventFeed } from './EventFeed';
export { renderThreatKpis } from './Kpi';
export { buildKpiData } from './parseMetrics';
export type { ThreatsKpiData, SecurityEvent, EventFeedStatus } from './types';
