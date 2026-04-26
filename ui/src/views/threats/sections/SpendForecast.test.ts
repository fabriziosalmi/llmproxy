import { describe, expect, it } from 'vitest';
import { renderSpendForecast, __testInternals, type SpendForecastBlock } from './SpendForecast';

const { _formatHours, _formatUsd, _classifyTimeToLimit } = __testInternals;

describe('SpendForecast helpers', () => {
    it('formats USD with 2 dp ≥ $1, 4 dp below', () => {
        expect(_formatUsd(12.345)).toBe('$12.35');
        expect(_formatUsd(0.0023)).toBe('$0.0023');
        expect(_formatUsd(null)).toBe('—');
    });

    it('formats hours into the most-readable unit', () => {
        expect(_formatHours(0.5)).toBe('30m');
        expect(_formatHours(2.25)).toBe('2h 15m');
        expect(_formatHours(48)).toBe('2d');
        expect(_formatHours(0)).toBe('over');
        expect(_formatHours(-1)).toBe('over');
        expect(_formatHours(null)).toBe('—');
    });

    it('classifies time_to_limit by alarm bucket', () => {
        const base: SpendForecastBlock = {
            current_spend_usd: 12, daily_limit_usd: 50, elapsed_hours: 6,
            burn_rate_usd_per_hour: 2, projected_daily_total_usd: 48,
            headroom_usd: 38, time_to_limit_hours: 19,
        };
        expect(_classifyTimeToLimit(base).intent).toBe('success');
        expect(_classifyTimeToLimit({ ...base, time_to_limit_hours: 3 }).intent).toBe('warning');
        expect(_classifyTimeToLimit({ ...base, time_to_limit_hours: 0.4 }).intent).toBe('danger');
        expect(_classifyTimeToLimit({ ...base, time_to_limit_hours: 0 }).intent).toBe('danger');
        expect(_classifyTimeToLimit({ ...base, time_to_limit_hours: 0 }).label).toBe('OVER LIMIT');
    });

    it('classifies "no limit set" as info — not danger', () => {
        const fc: SpendForecastBlock = {
            current_spend_usd: 5, daily_limit_usd: null, elapsed_hours: 4,
            burn_rate_usd_per_hour: 1.25, projected_daily_total_usd: 30,
            headroom_usd: null, time_to_limit_hours: null,
        };
        expect(_classifyTimeToLimit(fc).intent).toBe('info');
        expect(_classifyTimeToLimit(fc).label).toBe('no limit set');
    });

    it('classifies zero burn with limit as success — calm, not "out of data"', () => {
        const fc: SpendForecastBlock = {
            current_spend_usd: 0, daily_limit_usd: 50, elapsed_hours: 4,
            burn_rate_usd_per_hour: 0, projected_daily_total_usd: 0,
            headroom_usd: 50, time_to_limit_hours: null,
        };
        expect(_classifyTimeToLimit(fc).intent).toBe('success');
        expect(_classifyTimeToLimit(fc).label).toBe('no burn yet');
    });
});

describe('renderSpendForecast', () => {
    const _fc = (overrides: Partial<SpendForecastBlock> = {}): SpendForecastBlock => ({
        current_spend_usd: 12.0,
        daily_limit_usd: 50.0,
        elapsed_hours: 6.0,
        burn_rate_usd_per_hour: 2.0,
        projected_daily_total_usd: 48.0,
        headroom_usd: 38.0,
        time_to_limit_hours: 19.0,
        ...overrides,
    });

    it('renders 3 tiles — time / burn / projected — with values', () => {
        const host = document.createElement('div');
        renderSpendForecast(host, _fc());
        expect(host.querySelector('[data-testid="spend-forecast-time-tile"]')).not.toBeNull();
        expect(host.querySelector('[data-testid="spend-forecast-burn-tile"]')).not.toBeNull();
        expect(host.querySelector('[data-testid="spend-forecast-projected-tile"]')).not.toBeNull();
        // Time-to-limit headline
        expect(host.textContent).toContain('19h');
        // Burn rate
        expect(host.textContent).toContain('$2.00/h');
        // Projected
        expect(host.textContent).toContain('$48');
    });

    it('renders skeletons when data is null and no error', () => {
        const host = document.createElement('div');
        renderSpendForecast(host, null);
        // 3 tiles even in loading — no layout shift on first poll.
        expect(host.querySelectorAll('[data-skeleton]').length + host.querySelectorAll('article').length).toBeGreaterThan(0);
        // No live values yet.
        expect(host.textContent).not.toContain('$2.00');
    });

    it('renders only the time tile in error state — operator sees "fetch failed" once, not 3x', () => {
        const host = document.createElement('div');
        renderSpendForecast(host, null, '500 backend down');
        expect(host.querySelector('[data-testid="spend-forecast-time-tile"]')).not.toBeNull();
        expect(host.querySelector('[data-testid="spend-forecast-burn-tile"]')).toBeNull();
        expect(host.querySelector('[data-testid="spend-forecast-projected-tile"]')).toBeNull();
    });

    it('over-limit projection flips the projected tile to danger', () => {
        const host = document.createElement('div');
        renderSpendForecast(host, _fc({ projected_daily_total_usd: 120, headroom_usd: -70, time_to_limit_hours: 0 }));
        const projTile = host.querySelector('[data-testid="spend-forecast-projected-tile"]')!;
        // Danger intent → text-red-400 class on the value.
        expect(projTile.querySelector('.text-red-400')).not.toBeNull();
    });

    it('replaceChildren on re-render — no stacked grids', () => {
        const host = document.createElement('div');
        renderSpendForecast(host, _fc());
        renderSpendForecast(host, _fc({ time_to_limit_hours: 0.5 }));
        expect(host.querySelectorAll('[data-testid="spend-forecast-time-tile"]')).toHaveLength(1);
        expect(host.textContent).toContain('30m');
    });
});
