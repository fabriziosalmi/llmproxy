import { createMetricTile, type MetricIntent, type MetricSize } from '../../ui';
import type { ThreatsKpiData } from './types';

interface KpiSpec {
    key: keyof ThreatsKpiData | 'passRate';
    label: string;
    intent: MetricIntent;
    size: MetricSize;
    provenance: string;
    /** Pulls the formatted value from the data record. */
    format: (d: ThreatsKpiData) => { value: string; sub?: string };
}

const PRIMARY: KpiSpec[] = [
    {
        key: 'requests',
        label: 'Requests Today',
        intent: 'neutral',
        size: 'md',
        provenance: 'Sum of llm_proxy_requests_total since boot. Resets when the process restarts.',
        format: (d) => ({ value: d.requests.toLocaleString() }),
    },
    {
        key: 'blocked',
        label: 'Threats Blocked',
        intent: 'primary',
        size: 'md',
        provenance: 'llm_proxy_injection_blocked_total + llm_proxy_auth_failures_total. Counts WAF + auth rejections.',
        format: (d) => ({ value: d.blocked.toLocaleString() }),
    },
    {
        key: 'piiMasked',
        label: 'PII Masked',
        intent: 'warning',
        size: 'md',
        provenance: 'llm_proxy_injection_blocked_total — the same counter feeds the PII guard. Window: since boot.',
        format: (d) => ({ value: d.piiMasked.toLocaleString() }),
    },
    {
        key: 'passRate',
        label: 'Pass Rate',
        intent: 'success',
        size: 'md',
        provenance: '1 - (blocked / requests). 100% means no traffic was rejected.',
        format: (d) => ({ value: `${d.passRatePct.toFixed(1)}%` }),
    },
];

const SECONDARY: KpiSpec[] = [
    {
        key: 'errors',
        label: 'Errors',
        intent: 'danger',
        size: 'sm',
        provenance: 'llm_proxy_request_errors_total — upstream 5xx + timeouts since boot.',
        format: (d) => ({ value: d.errors.toLocaleString() }),
    },
    {
        key: 'tokens',
        label: 'Tokens',
        intent: 'info',
        size: 'sm',
        provenance: 'llm_proxy_token_usage_total — prompt + completion tokens billed since boot.',
        format: (d) => ({ value: d.tokens > 1000 ? `${(d.tokens / 1000).toFixed(1)}k` : d.tokens.toLocaleString() }),
    },
    {
        key: 'uptimeSeconds',
        label: 'Uptime',
        intent: 'info',
        size: 'sm',
        provenance: 'GET /health uptime_seconds — time since the proxy process started.',
        format: (d) => {
            if (d.uptimeSeconds === null) return { value: '—' };
            const h = Math.floor(d.uptimeSeconds / 3600);
            const m = Math.floor((d.uptimeSeconds % 3600) / 60);
            return { value: h > 0 ? `${h}h ${m}m` : `${m}m` };
        },
    },
    {
        key: 'pool',
        label: 'Healthy Endpoints',
        intent: 'success',
        size: 'sm',
        provenance: 'GET /health pool_healthy / pool_size — endpoints currently passing health checks.',
        format: (d) => {
            if (!d.pool) return { value: '—' };
            return { value: `${d.pool.healthy}/${d.pool.total}` };
        },
    },
];

function makeGrid(specs: KpiSpec[], data: ThreatsKpiData | null, error?: string): HTMLElement {
    const grid = document.createElement('div');
    grid.className = 'grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6';
    for (const spec of specs) {
        const tile = createMetricTile({
            label: spec.label,
            value: data ? spec.format(data).value : '',
            sub: data ? spec.format(data).sub : undefined,
            intent: spec.intent,
            size: spec.size,
            provenance: spec.provenance,
            loading: data === null && !error,
            error,
            testId: `kpi-${String(spec.key)}`,
        });
        grid.appendChild(tile);
    }
    return grid;
}

/**
 * Mount the KPI grid into the given container. Subsequent calls replace the
 * children, so callers may pass `null` to render the loading state and a
 * populated record when the metrics arrive.
 */
export function renderThreatKpis(container: HTMLElement, data: ThreatsKpiData | null, error?: string): void {
    container.replaceChildren(makeGrid(PRIMARY, data, error), makeGrid(SECONDARY, data, error));
}
