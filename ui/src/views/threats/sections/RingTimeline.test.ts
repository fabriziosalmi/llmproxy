import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { renderRingTimeline } from './RingTimeline';

let host: HTMLElement;

beforeEach(() => {
    host = document.createElement('div');
    document.body.appendChild(host);
});

afterEach(() => {
    host.remove();
});

describe('renderRingTimeline', () => {
    it('shows the placeholder when no traces', () => {
        renderRingTimeline(host, []);
        expect(host.textContent).toContain('No request traces yet');
    });

    it('renders one row per trace with timestamp, req_id, total, and a TTFT badge when streaming', () => {
        renderRingTimeline(host, [
            {
                timestamp: 1714047296,
                req_id: 'req-1',
                total_ms: 240,
                upstream_ms: 180,
                ttft_ms: 90,
                rings: {
                    ingress: { duration_ms: 5, plugins: [{ name: 'firewall', ms: 4 }] },
                    pre_flight: { duration_ms: 50 },
                    routing: { duration_ms: 5 },
                },
            },
            {
                timestamp: 1714047297,
                req_id: 'req-2',
                total_ms: 80,
                rings: { ingress: { duration_ms: 5 } },
            },
        ]);
        const rows = host.querySelectorAll('[data-req-id]');
        expect(rows).toHaveLength(2);
        // First row carries the TTFT badge.
        expect(rows[0]?.textContent).toContain('TTFT 90ms');
        expect(rows[0]?.textContent).toContain('240ms');
        expect(rows[1]?.textContent).not.toContain('TTFT');
    });

    it('handles a trace with empty rings without crashing', () => {
        renderRingTimeline(host, [{ timestamp: 1, req_id: 'r', total_ms: 0, rings: {} }]);
        expect(host.querySelector('[data-req-id="r"]')).not.toBeNull();
    });
});
