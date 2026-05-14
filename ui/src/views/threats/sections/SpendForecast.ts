/**
 * Threats → Spend Forecast (P.2)
 *
 * Surfaces the most actionable single number an operator looks for in
 * the budget area: "at this rate, the daily limit hits in N hours".
 * Backend M.2 already serves the math via /api/v1/analytics/forecast;
 * this view renders it next to the existing Budget Gauge.
 *
 * Render rules (intent-coded for at-a-glance triage):
 *   time_to_limit_hours < 1   → danger (rose) — alarm
 *   time_to_limit_hours < 4   → warning (amber)
 *   else                      → success (emerald) — calm
 *   null (insufficient data)  → neutral
 *   0    (already over limit) → danger
 *   no limit configured       → info, just shows projection
 */

import { createMetricTile, type MetricIntent } from '../../../ui';

export interface SpendForecastBlock {
    current_spend_usd: number;
    daily_limit_usd: number | null;
    elapsed_hours: number;
    burn_rate_usd_per_hour: number | null;
    projected_daily_total_usd: number | null;
    headroom_usd: number | null;
    time_to_limit_hours: number | null;
}

function _formatUsd(v: number | null): string {
    if (v === null || v === undefined) return '—';
    if (v >= 1) return `$${v.toFixed(2)}`;
    return `$${v.toFixed(4)}`;
}

function _formatHours(v: number | null): string {
    if (v === null) return '—';
    if (v <= 0) return 'over';
    if (v < 1) return `${Math.round(v * 60)}m`;
    if (v < 24) {
        const h = Math.floor(v);
        const m = Math.round((v - h) * 60);
        return m > 0 ? `${h}h ${m}m` : `${h}h`;
    }
    const d = Math.floor(v / 24);
    const h = Math.round(v % 24);
    return h > 0 ? `${d}d ${h}h` : `${d}d`;
}

function _classifyTimeToLimit(forecast: SpendForecastBlock): { intent: MetricIntent; label: string } {
    const ttl = forecast.time_to_limit_hours;
    if (forecast.daily_limit_usd === null) {
        return { intent: 'info', label: 'no limit set' };
    }
    if (ttl === null) {
        // Either insufficient data (elapsed < 5min) or zero burn with headroom.
        if (forecast.burn_rate_usd_per_hour === 0) return { intent: 'success', label: 'no burn yet' };
        return { intent: 'neutral', label: 'gathering data' };
    }
    if (ttl <= 0) return { intent: 'danger', label: 'OVER LIMIT' };
    if (ttl < 1) return { intent: 'danger', label: _formatHours(ttl) };
    if (ttl < 4) return { intent: 'warning', label: _formatHours(ttl) };
    return { intent: 'success', label: _formatHours(ttl) };
}

export interface SpendForecastApi {
    fetchSpendForecast: () => Promise<SpendForecastBlock>;
}

export function renderSpendForecast(host: HTMLElement, forecast: SpendForecastBlock | null, error?: string): void {
    host.replaceChildren();
    host.className = 'grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6';
    host.setAttribute('data-testid', 'spend-forecast-grid');

    if (forecast === null && !error) {
        // Loading state — three skeleton tiles to avoid layout shift.
        for (const label of ['Time to Limit', 'Burn Rate', 'Projected (24h)']) {
            host.appendChild(createMetricTile({ label, value: '', loading: true }));
        }
        return;
    }

    if (error) {
        host.appendChild(
            createMetricTile({
                label: 'Time to Limit',
                value: '',
                error,
                testId: 'spend-forecast-time-tile',
            })
        );
        return;
    }

    const fc = forecast!;
    const cls = _classifyTimeToLimit(fc);

    // Tile 1 — the headline. Big-number "time to limit".
    host.appendChild(
        createMetricTile({
            label: 'Time to Limit',
            value: cls.label,
            intent: cls.intent,
            sub:
                fc.daily_limit_usd !== null
                    ? `${_formatUsd(fc.current_spend_usd)} of ${_formatUsd(fc.daily_limit_usd)}`
                    : `spent ${_formatUsd(fc.current_spend_usd)} so far`,
            provenance:
                'GET /api/v1/analytics/forecast: headroom_usd ÷ burn_rate_usd_per_hour. ' +
                'null means insufficient data (< 5 min elapsed) or zero rate.',
            testId: 'spend-forecast-time-tile',
        })
    );

    // Tile 2 — current burn rate.
    host.appendChild(
        createMetricTile({
            label: 'Burn Rate',
            value: fc.burn_rate_usd_per_hour !== null ? `${_formatUsd(fc.burn_rate_usd_per_hour)}/h` : '—',
            intent: 'info',
            sub: `over ${fc.elapsed_hours.toFixed(1)}h`,
            provenance: 'current_spend_usd ÷ elapsed_hours since local midnight.',
            testId: 'spend-forecast-burn-tile',
        })
    );

    // Tile 3 — projected 24h total.
    host.appendChild(
        createMetricTile({
            label: 'Projected (24h)',
            value: fc.projected_daily_total_usd !== null ? _formatUsd(fc.projected_daily_total_usd) : '—',
            intent:
                fc.daily_limit_usd !== null &&
                fc.projected_daily_total_usd !== null &&
                fc.projected_daily_total_usd > fc.daily_limit_usd
                    ? 'danger'
                    : 'neutral',
            sub:
                fc.headroom_usd !== null
                    ? fc.headroom_usd >= 0
                        ? `${_formatUsd(fc.headroom_usd)} headroom`
                        : `${_formatUsd(-fc.headroom_usd)} over`
                    : 'no limit',
            provenance: 'burn_rate × 24. Compared with daily_limit_usd to flag projected overage.',
            testId: 'spend-forecast-projected-tile',
        })
    );
}

// Pure helpers exported for the test suite — keep the rendering logic
// thin and the math centrally testable.
export const __testInternals = { _formatUsd, _formatHours, _classifyTimeToLimit };
