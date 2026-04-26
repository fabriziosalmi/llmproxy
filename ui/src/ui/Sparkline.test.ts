import { describe, expect, it } from 'vitest';
import { createSparkline } from './Sparkline';

describe('createSparkline', () => {
    it('returns an SVG with viewBox + role=img and the configured height', () => {
        const svg = createSparkline({ data: [1, 2, 3, 4], height: 32 });
        expect(svg.tagName.toLowerCase()).toBe('svg');
        expect(svg.getAttribute('role')).toBe('img');
        expect(svg.getAttribute('height')).toBe('32');
        expect(svg.getAttribute('viewBox')).toMatch(/^0 0 \d+ 32$/);
    });

    it('renders nothing meaningful for fewer than 2 data points', () => {
        const svg = createSparkline({ data: [42] });
        expect(svg.querySelector('polyline')).toBeNull();
        expect(svg.querySelector('path')).toBeNull();
    });

    it('emits a polyline with one point per data entry', () => {
        const svg = createSparkline({ data: [0, 5, 10, 5, 0], height: 28, aspect: 12 });
        const line = svg.querySelector('polyline');
        expect(line).not.toBeNull();
        const points = (line!.getAttribute('points') ?? '').trim().split(/\s+/);
        expect(points).toHaveLength(5);
    });

    it('flat series sits at the vertical mid-line (no div-by-zero)', () => {
        const svg = createSparkline({ data: [7, 7, 7, 7], height: 28 });
        const points = (svg.querySelector('polyline')!.getAttribute('points') ?? '').split(/\s+/);
        // Every point's y should be ~the midpoint (around 14 with pad=1, h=28).
        for (const p of points) {
            const [, y] = p.split(',');
            expect(Number(y)).toBeGreaterThan(13);
            expect(Number(y)).toBeLessThan(15);
        }
    });

    it('emits an area path + linearGradient when area is on (default)', () => {
        const svg = createSparkline({ data: [1, 2, 3] });
        expect(svg.querySelector('linearGradient')).not.toBeNull();
        expect(svg.querySelector('path')).not.toBeNull();
        // Area path closes back to bottom corners.
        const d = svg.querySelector('path')!.getAttribute('d') ?? '';
        expect(d).toContain('Z');
    });

    it('omits the area path when area=false', () => {
        const svg = createSparkline({ data: [1, 2, 3], area: false });
        expect(svg.querySelector('linearGradient')).toBeNull();
        expect(svg.querySelector('path')).toBeNull();
        // The polyline still exists.
        expect(svg.querySelector('polyline')).not.toBeNull();
    });

    it('the stroke color follows the named palette token', () => {
        const cyan = createSparkline({ data: [1, 2], color: 'cyan' });
        const rose = createSparkline({ data: [1, 2], color: 'rose' });
        expect(cyan.querySelector('polyline')!.getAttribute('stroke')).toBe('#22d3ee');
        expect(rose.querySelector('polyline')!.getAttribute('stroke')).toBe('#fb7185');
    });

    it('aria-label falls back to "Trend" when not provided', () => {
        const svg = createSparkline({ data: [1, 2] });
        expect(svg.getAttribute('aria-label')).toBe('Trend');
    });

    it('honors a custom ariaLabel', () => {
        const svg = createSparkline({ data: [1, 2], ariaLabel: 'Active endpoints over 24h' });
        expect(svg.getAttribute('aria-label')).toBe('Active endpoints over 24h');
    });
});
