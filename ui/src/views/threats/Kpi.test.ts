import { describe, expect, it } from 'vitest';
import { renderThreatKpis } from './Kpi';
import type { ThreatsKpiData } from './types';

const _data: ThreatsKpiData = {
    requests: 1234,
    blocked: 8,
    piiMasked: 3,
    passRatePct: 99.4,
    errors: 2,
    tokens: 12500,
    uptimeSeconds: 3600,
    pool: { total: 3, healthy: 2 },
};

describe('renderThreatKpis', () => {
    it('renders 4 primary + 4 secondary tiles with values', () => {
        const host = document.createElement('div');
        renderThreatKpis(host, _data);
        expect(host.querySelector('[data-testid="kpi-requests"]')).not.toBeNull();
        expect(host.querySelector('[data-testid="kpi-blocked"]')).not.toBeNull();
        expect(host.querySelector('[data-testid="kpi-piiMasked"]')).not.toBeNull();
        expect(host.querySelector('[data-testid="kpi-passRate"]')).not.toBeNull();
        expect(host.querySelector('[data-testid="kpi-errors"]')).not.toBeNull();
        expect(host.querySelector('[data-testid="kpi-tokens"]')).not.toBeNull();
    });

    it('Q.3 — sparkline series ≥ 2 points wires onto the matching tile', () => {
        const host = document.createElement('div');
        const series: Record<string, number[]> = {
            requests: Array.from({ length: 24 }, (_, i) => i * 5),
            blocked: Array.from({ length: 24 }, () => 0)
                .concat([3])
                .slice(-24),
            errors: Array.from({ length: 24 }, () => 0)
                .concat([1, 2])
                .slice(-24),
        };
        renderThreatKpis(host, _data, undefined, series);

        const reqTile = host.querySelector('[data-testid="kpi-requests"]')!;
        // Sparkline is an SVG inside the tile.
        expect(reqTile.querySelector('svg[role="img"]')).not.toBeNull();

        const blockedTile = host.querySelector('[data-testid="kpi-blocked"]')!;
        expect(blockedTile.querySelector('svg[role="img"]')).not.toBeNull();

        const errorsTile = host.querySelector('[data-testid="kpi-errors"]')!;
        expect(errorsTile.querySelector('svg[role="img"]')).not.toBeNull();
    });

    it('passRate tile has NO sparkline — derived metric, no independent series', () => {
        const host = document.createElement('div');
        renderThreatKpis(host, _data, undefined, {
            requests: Array.from({ length: 24 }, (_, i) => i),
        });
        const passTile = host.querySelector('[data-testid="kpi-passRate"]')!;
        expect(passTile.querySelector('svg[role="img"]')).toBeNull();
    });

    it('series < 2 points does NOT render a sparkline (avoids degenerate strip)', () => {
        const host = document.createElement('div');
        renderThreatKpis(host, _data, undefined, { requests: [42] });
        const reqTile = host.querySelector('[data-testid="kpi-requests"]')!;
        expect(reqTile.querySelector('svg[role="img"]')).toBeNull();
    });

    it('missing series map → tiles render without sparklines (back-compat path)', () => {
        const host = document.createElement('div');
        renderThreatKpis(host, _data); // no series arg
        const reqTile = host.querySelector('[data-testid="kpi-requests"]')!;
        expect(reqTile.querySelector('svg[role="img"]')).toBeNull();
    });

    it('loading state suppresses sparklines even with series data — no spark over a skeleton', () => {
        const host = document.createElement('div');
        renderThreatKpis(host, null, undefined, { requests: [1, 2, 3] });
        const reqTile = host.querySelector('[data-testid="kpi-requests"]')!;
        expect(reqTile.querySelector('svg[role="img"]')).toBeNull();
    });
});
