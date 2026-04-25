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
import { mountThreatChart, type ThreatChartHandle } from './sections/ThreatChart';
import type { GuardsStatus, LatencyMetrics, TimelinePayload } from './sections/types';

interface ThreatsApi {
    fetchMetrics: () => Promise<string>;
    fetchHealth: () => Promise<{ uptime_seconds?: number; pool_size?: number; pool_healthy?: number } | null>;
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

    renderThreatKpis(container, null);

    const refresh = async () => {
        try {
            const [text, health] = await Promise.all([
                opts.api.fetchMetrics().catch(() => ''),
                opts.api.fetchHealth().catch(() => null),
            ]);
            lastData = buildKpiData(text, health);
            lastError = undefined;
            renderThreatKpis(container, lastData);
        } catch (err) {
            lastError = (err as Error)?.message || 'Backend unreachable';
            // Keep the last good data on screen if we have any; otherwise show errors.
            renderThreatKpis(container, lastData, lastData ? undefined : lastError);
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

export interface SectionsApi {
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

    const refresh = async (): Promise<void> => {
        const [promText, guards, latency, timeline] = await Promise.all([
            opts.api.fetchMetrics().catch(() => ''),
            opts.api.fetchGuardsStatus().catch(() => null),
            opts.api.fetchLatencyMetrics().catch(() => null),
            opts.api.fetchRingTimeline().catch(() => null),
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

export { ThreatEventFeed } from './EventFeed';
export { renderThreatKpis } from './Kpi';
export { buildKpiData } from './parseMetrics';
export type { ThreatsKpiData, SecurityEvent, EventFeedStatus } from './types';
