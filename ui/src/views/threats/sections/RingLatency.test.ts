import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { renderRingLatencyBars, renderTtft } from './RingLatency';

let host: HTMLElement;

beforeEach(() => {
    host = document.createElement('div');
    document.body.appendChild(host);
});

afterEach(() => {
    host.remove();
});

describe('renderRingLatencyBars', () => {
    it('shows the placeholder when every ring has zero count', () => {
        renderRingLatencyBars(host, { rings: {} });
        expect(host.textContent).toContain('Collecting samples');
    });

    it('renders a bar per ring with P50/P95/P99 + count', () => {
        renderRingLatencyBars(host, {
            rings: {
                ingress: { p50: 1, p95: 4, p99: 9, count: 100 },
                pre_flight: { p50: 0.5, p95: 2, p99: 5, count: 50 },
                routing: { p50: 0, p95: 0, p99: 0, count: 0 },
                post_flight: { p50: 0, p95: 0, p99: 0, count: 0 },
                background: { p50: 0, p95: 0, p99: 0, count: 0 },
            },
        });
        expect(host.querySelector('[data-testid="ring-ingress"]')).not.toBeNull();
        expect(host.querySelector('[data-testid="ring-pre_flight"]')).not.toBeNull();
        expect(host.textContent).toContain('INGRESS');
        expect(host.textContent).toContain('PRE-FLIGHT');
        expect(host.textContent).toContain('1.0ms');
        expect(host.textContent).toContain('100x');
    });
});

describe('renderTtft', () => {
    it('shows the placeholder when no streaming samples', () => {
        renderTtft(host, { samples: 0 });
        expect(host.textContent).toContain('No streaming data yet');
    });

    it('renders P50 / P95 / P99 + sample count', () => {
        renderTtft(host, { p50: 120, p95: 300, p99: 800, samples: 42 });
        expect(host.textContent).toContain('120');
        expect(host.textContent).toContain('300');
        expect(host.textContent).toContain('800');
        expect(host.textContent).toContain('42 stream samples');
    });

    it('flips P95 color above 1000ms (rose)', () => {
        renderTtft(host, { p50: 100, p95: 1500, p99: 2000, samples: 5 });
        // The P95 value's class should include rose tone.
        const html = host.innerHTML;
        expect(html).toContain('text-rose-400');
    });
});
