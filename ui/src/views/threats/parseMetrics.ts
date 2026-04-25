import type { ThreatsKpiData } from './types';

interface RawHealth {
    uptime_seconds?: number;
    pool_size?: number;
    pool_healthy?: number;
}

/** Sum a Prometheus counter across all label sets. */
export function extractMetric(promText: string, name: string): number {
    let total = 0;
    for (const line of promText.split('\n')) {
        if (line.startsWith('#') || line === '' || !line.startsWith(name)) continue;
        const parts = line.split(' ');
        const last = parts[parts.length - 1];
        if (last === undefined) continue;
        const val = Number.parseFloat(last);
        if (!Number.isNaN(val)) total += val;
    }
    return total;
}

export function buildKpiData(promText: string, health: RawHealth | null): ThreatsKpiData {
    const requests = extractMetric(promText, 'llm_proxy_requests_total');
    const blocked = extractMetric(promText, 'llm_proxy_injection_blocked_total');
    const authFails = extractMetric(promText, 'llm_proxy_auth_failures_total');
    const totalBlocked = blocked + authFails;
    const errors = extractMetric(promText, 'llm_proxy_request_errors_total');
    const tokens = extractMetric(promText, 'llm_proxy_token_usage_total');
    const passRatePct = requests > 0 ? Math.max(0, (1 - totalBlocked / requests) * 100) : 100;
    const uptimeSeconds = health?.uptime_seconds ?? null;
    const pool = health ? { healthy: health.pool_healthy ?? 0, total: health.pool_size ?? 0 } : null;
    return {
        requests,
        blocked: totalBlocked,
        piiMasked: blocked,
        passRatePct,
        errors,
        tokens,
        uptimeSeconds,
        pool,
    };
}
