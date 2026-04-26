import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mountHealthPanel, type HealthResponse } from './HealthPanel';

const _ok: HealthResponse = {
    status: 'ok',
    version: '1.21.24',
    uptime_seconds: 120,
    components: {
        endpoints: { status: 'ok', total: 2, healthy: 2, circuits_open: 0 },
        store: { status: 'ok' },
        cache: { status: 'ok', size: 12, hits: 4, misses: 8 },
        plugins: { status: 'ok', loaded: 5, ring_count: { ingress: 1, pre_flight: 2, post_flight: 2 } },
        session: { status: 'ok' },
        log_queue: { status: 'ok', depth: 5, max: 100, saturation: 0.05 },
    },
};

let host: HTMLElement;

beforeEach(() => {
    host = document.createElement('div');
    document.body.appendChild(host);
});
afterEach(() => {
    host.remove();
});

describe('mountHealthPanel', () => {
    it('renders one tile per component with status badge + label', async () => {
        const handle = mountHealthPanel(host, { fetchHealth: vi.fn().mockResolvedValue(_ok) });
        await handle.refresh();

        for (const name of ['endpoints', 'store', 'cache', 'plugins', 'session', 'log_queue']) {
            expect(host.querySelector(`[data-testid="health-component-${name}"]`)).not.toBeNull();
        }
        // Overall badge in the header reflects body.status
        expect(host.querySelector('[data-testid="health-overall-badge"]')?.textContent).toContain('ok');
    });

    it('formats endpoints detail as "healthy / total" + circuit-open count', async () => {
        const handle = mountHealthPanel(host, {
            fetchHealth: vi.fn().mockResolvedValue({
                ..._ok,
                components: { ..._ok.components, endpoints: { status: 'degraded', total: 3, healthy: 1, circuits_open: 1 } },
            }),
        });
        await handle.refresh();
        const tile = host.querySelector('[data-testid="health-component-endpoints"]')!;
        expect(tile.textContent).toContain('1 / 3 healthy');
        expect(tile.textContent).toContain('1 circuit OPEN');
    });

    it('flags log_queue saturation as a percent', async () => {
        const handle = mountHealthPanel(host, {
            fetchHealth: vi.fn().mockResolvedValue({
                ..._ok,
                components: {
                    ..._ok.components,
                    log_queue: { status: 'degraded', depth: 85, max: 100, saturation: 0.85 },
                },
            }),
        });
        await handle.refresh();
        const tile = host.querySelector('[data-testid="health-component-log_queue"]')!;
        expect(tile.textContent).toContain('85 / 100');
        expect(tile.textContent).toContain('85%');
    });

    it('down components carry the danger border', async () => {
        const handle = mountHealthPanel(host, {
            fetchHealth: vi.fn().mockResolvedValue({
                ..._ok,
                status: 'down',
                components: { ..._ok.components, session: { status: 'down' } },
            }),
        });
        await handle.refresh();
        const sess = host.querySelector('[data-testid="health-component-session"]')!;
        expect(sess.className).toContain('border-rose-500/25');
    });

    it('error state surfaces on fetch failure with retry hook', async () => {
        const handle = mountHealthPanel(host, {
            fetchHealth: vi.fn().mockRejectedValue(new Error('500 backend')),
        });
        await handle.refresh();
        expect(host.querySelector('[data-testid="health-error"]')).not.toBeNull();
        expect(host.textContent).toContain('500 backend');
    });

    it('older proxies without components block render an explainer', async () => {
        const handle = mountHealthPanel(host, {
            fetchHealth: vi.fn().mockResolvedValue({ status: 'ok', version: '1.20.0' }),
        });
        await handle.refresh();
        expect(host.querySelector('[data-testid="health-no-components"]')).not.toBeNull();
        expect(host.textContent).toContain('1.21.9');
    });

    it('forward-compat: unknown component keys are rendered at the end', async () => {
        const handle = mountHealthPanel(host, {
            fetchHealth: vi.fn().mockResolvedValue({
                ..._ok,
                components: { ..._ok.components, mystery: { status: 'ok' } },
            }),
        });
        await handle.refresh();
        // 6 known + 1 unknown = 7 tiles
        expect(host.querySelectorAll('[data-testid^="health-component-"]')).toHaveLength(7);
        expect(host.querySelector('[data-testid="health-component-mystery"]')).not.toBeNull();
    });
});
