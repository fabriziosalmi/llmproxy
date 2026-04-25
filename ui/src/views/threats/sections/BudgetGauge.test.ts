import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { computeBudget, renderBudgetGauge } from './BudgetGauge';

let host: HTMLElement;

beforeEach(() => {
    host = document.createElement('div');
    document.body.appendChild(host);
});

afterEach(() => {
    host.remove();
});

describe('computeBudget', () => {
    it('returns { configured: false } when no signal at all', () => {
        expect(computeBudget({ cost: 0, consumed: 0, limit: 0 })).toEqual({ configured: false });
    });

    it('uses guardsStatus.budget.total_cost_today over the /metrics counter', () => {
        const r = computeBudget({
            cost: 0,
            consumed: 0,
            limit: 0,
            guardsStatus: { budget: { total_cost_today: 12.5, daily_limit: 50 } },
        });
        expect(r).toMatchObject({ consumed: 12.5, limit: 50 });
    });

    it('color goes emerald → amber → rose with usage', () => {
        const low = computeBudget({ cost: 0, consumed: 1, limit: 100 });
        const mid = computeBudget({ cost: 0, consumed: 60, limit: 100 });
        const hi = computeBudget({ cost: 0, consumed: 90, limit: 100 });
        expect((low as { color: string }).color).toBe('emerald');
        expect((mid as { color: string }).color).toBe('amber');
        expect((hi as { color: string }).color).toBe('rose');
    });

    it('caps pct at 100 when consumed exceeds limit', () => {
        const r = computeBudget({ cost: 0, consumed: 250, limit: 100 });
        expect((r as { pct: number }).pct).toBe(100);
    });

    it('returns null remaining when there is no limit (tracking-only mode)', () => {
        const r = computeBudget({ cost: 5, consumed: 5, limit: 0 });
        expect((r as { remaining: number | null }).remaining).toBeNull();
    });
});

describe('renderBudgetGauge', () => {
    it('renders the empty hint when nothing is configured', () => {
        renderBudgetGauge(host, { cost: 0, consumed: 0, limit: 0 });
        expect(host.textContent).toContain('No budget configured');
        expect(host.querySelector('[data-testid="budget-gauge-bar"]')).toBeNull();
    });

    it('renders the bar + remaining when limit is set', () => {
        renderBudgetGauge(host, { cost: 1, consumed: 25, limit: 100 });
        const bar = host.querySelector<HTMLElement>('[data-testid="budget-gauge-bar"]');
        expect(bar).not.toBeNull();
        expect(bar?.style.width).toBe('25%');
        expect(host.textContent).toContain('$75.00 remaining');
        expect(host.textContent).toContain('25% used');
    });

    it('shows "tracking" when limit=0 but cost is positive', () => {
        renderBudgetGauge(host, { cost: 1.5, consumed: 0, limit: 0 });
        expect(host.textContent).toContain('tracking');
        expect(host.querySelector('[data-testid="budget-gauge-bar"]')).toBeNull();
    });
});
