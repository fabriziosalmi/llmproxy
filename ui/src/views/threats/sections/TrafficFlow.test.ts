import { describe, expect, it } from 'vitest';
import { renderTrafficFlow, __testInternals, type FlowData } from './TrafficFlow';

const { _weightToWidth } = __testInternals;

const _data = (overrides: Partial<FlowData> = {}): FlowData => ({
    clientsLabel: '1.2k',
    clientsSub: 'req · 4 blk',
    guards: [
        { id: 'firewall', label: 'Firewall', sub: 'WAF · L1', state: 'live' },
        { id: 'injection_guard', label: 'Injection', sub: 'L2', state: 'live' },
    ],
    router: { id: 'router', label: 'Router', sub: 'smart', state: 'live' },
    providers: [
        { id: 'openai', label: 'openai', sub: 'LIVE', state: 'live' },
        { id: 'anthropic', label: 'anthropic', sub: 'OPEN', state: 'down' },
    ],
    ...overrides,
});

describe('renderTrafficFlow', () => {
    it('mounts a single card with the SVG inside', () => {
        const host = document.createElement('div');
        renderTrafficFlow(host, _data());
        expect(host.querySelector('[data-testid="traffic-flow-card"]')).not.toBeNull();
        expect(host.querySelector('[data-testid="flow-svg"]')).not.toBeNull();
    });

    it('replaceChildren on re-render — no stacked cards', () => {
        const host = document.createElement('div');
        renderTrafficFlow(host, _data());
        renderTrafficFlow(host, _data());
        expect(host.querySelectorAll('[data-testid="traffic-flow-card"]')).toHaveLength(1);
    });

    it('renders one node per guard + each provider + clients + router', () => {
        const host = document.createElement('div');
        renderTrafficFlow(host, _data());
        const nodes = host.querySelectorAll('[data-node-id]');
        // 1 clients + 2 guards + 1 router + 2 providers = 6
        expect(nodes).toHaveLength(6);
        expect(host.querySelector('[data-node-id="firewall"]')).not.toBeNull();
        expect(host.querySelector('[data-node-id="openai"]')).not.toBeNull();
    });

    it('blocked guards stamp a pulse-live halo', () => {
        const host = document.createElement('div');
        renderTrafficFlow(
            host,
            _data({
                guards: [{ id: 'firewall', label: 'Firewall', state: 'blocked' }],
            })
        );
        const guard = host.querySelector('[data-node-id="firewall"]')!;
        // The halo rect is the second rect with no fill + .pulse-live class.
        const halos = guard.querySelectorAll('.pulse-live');
        expect(halos.length).toBeGreaterThan(0);
    });

    it('idle guards do NOT pulse', () => {
        const host = document.createElement('div');
        renderTrafficFlow(
            host,
            _data({
                guards: [{ id: 'link_sanitizer', label: 'Link', state: 'idle' }],
            })
        );
        const guard = host.querySelector('[data-node-id="link_sanitizer"]')!;
        expect(guard.querySelectorAll('.pulse-live')).toHaveLength(0);
    });

    it('renders an onboarding placeholder provider when none exist', () => {
        const host = document.createElement('div');
        renderTrafficFlow(host, _data({ providers: [] }));
        // Empty providers array: the orchestrator-built data injects a single
        // {id: 'none'} placeholder in _buildFlowData; here we test the render
        // path doesn't blow up with zero providers — call directly.
        const nodes = host.querySelectorAll('[data-node-id]');
        // 1 clients + 2 guards + 1 router + 0 providers = 4
        expect(nodes.length).toBe(4);
    });

    it('caption is rendered in the header when supplied', () => {
        const host = document.createElement('div');
        renderTrafficFlow(host, _data(), { caption: 'last 60s · 1.2k req · 4 blk' });
        expect(host.textContent).toContain('last 60s · 1.2k req · 4 blk');
    });
});

// R.3 — Sankey-style weighted ribbons.
describe('TrafficFlow ribbon weights', () => {
    it('_weightToWidth maps 0→1.25px, 1→6px, with smooth interpolation', () => {
        expect(_weightToWidth(0)).toBeCloseTo(1.25);
        expect(_weightToWidth(1)).toBeCloseTo(6.0);
        expect(_weightToWidth(0.5)).toBeCloseTo(3.625);
    });

    it('_weightToWidth clamps out-of-range inputs', () => {
        expect(_weightToWidth(-5)).toBeCloseTo(1.25);
        expect(_weightToWidth(99)).toBeCloseTo(6.0);
    });

    it('_weightToWidth defaults to max width on undefined (back-compat)', () => {
        expect(_weightToWidth(undefined)).toBeCloseTo(6.0);
    });

    it('renders ribbons with stroke-widths derived from node weights', () => {
        const host = document.createElement('div');
        renderTrafficFlow(host, {
            clientsLabel: '1k',
            guards: [
                { id: 'fw', label: 'FW', state: 'live', weight: 1.0 },
                { id: 'lk', label: 'LK', state: 'idle', weight: 0.3 },
            ],
            router: { id: 'router', label: 'Router', state: 'live', weight: 1.0 },
            providers: [
                { id: 'openai', label: 'openai', state: 'live', weight: 1.0 },
                { id: 'flaky', label: 'flaky', state: 'down', weight: 0.45 },
            ],
        });

        // Edge layer is the first <g> child after the column-header texts;
        // each path's stroke-width should reflect _weightToWidth(weight).
        const paths = host.querySelectorAll<SVGPathElement>('path[stroke-linecap="round"]');
        expect(paths.length).toBeGreaterThan(0);
        // Collect unique stroke-widths — should span at least 2 distinct values
        // because the data contains both weight=1.0 and weight=0.3 nodes.
        const widths = new Set<string>();
        paths.forEach((p) => widths.add(p.getAttribute('stroke-width') ?? ''));
        expect(widths.size).toBeGreaterThanOrEqual(2);
    });
});
