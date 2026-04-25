/**
 * Threats view orchestrator. Imported from `components/threats.js` (the
 * existing JS shell) and called after `initThreats()` so the new TS-rendered
 * KPI grid and event feed take over their respective DOM mount points.
 *
 * Strangler fig: budget gauge, firewall stats, endpoint breakdown, ring
 * latency and threat chart still live in the legacy module. They migrate
 * incrementally — this file is the template.
 */
import { renderThreatKpis } from './Kpi';
import { ThreatEventFeed } from './EventFeed';
import { buildKpiData } from './parseMetrics';
import type { ThreatsKpiData } from './types';

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

export { ThreatEventFeed } from './EventFeed';
export { renderThreatKpis } from './Kpi';
export { buildKpiData } from './parseMetrics';
export type { ThreatsKpiData, SecurityEvent, EventFeedStatus } from './types';
