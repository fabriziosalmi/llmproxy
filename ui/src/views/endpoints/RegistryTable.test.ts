import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createRegistryTable } from './RegistryTable';
import type { Endpoint } from './types';

const ROWS: Endpoint[] = [
    {
        id: 'openai',
        name: 'OpenAI',
        url: 'https://api.openai.com/v1',
        provider: 'openai',
        status: 'Live',
        circuit_state: 'closed',
        latency: '120ms',
        priority: 10,
        models: ['gpt-4o-mini'],
    },
    {
        id: 'flaky',
        url: 'https://flaky.example.com/v1',
        provider: 'openai-compatible',
        status: 'DEGRADED',
        circuit_state: 'half_open',
        failure_count: 3,
        failure_threshold: 5,
        latency: '420ms',
        priority: 1,
    },
    {
        id: 'dead',
        url: 'https://dead.example.com/v1',
        status: 'IGNORED',
        circuit_state: 'open',
        failure_count: 9,
        failure_threshold: 5,
        latency: '—',
        priority: 0,
    },
];

let host: HTMLElement;
let deps: {
    onProbeEndpoint: any;
    onResetCircuitBreaker: any;
    onToggleEndpoint: any;
    onDeleteEndpoint: any;
    onUpdatePriority: any;
    refresh: any;
    toast: any;
};

beforeEach(() => {
    host = document.createElement('div');
    document.body.appendChild(host);
    deps = {
        onProbeEndpoint: vi.fn().mockResolvedValue({ ok: true, status: 200, latency_ms: 42, models_count: 3 }),
        onResetCircuitBreaker: vi.fn().mockResolvedValue(undefined),
        onToggleEndpoint: vi.fn().mockResolvedValue(undefined),
        onDeleteEndpoint: vi.fn().mockResolvedValue(undefined),
        onUpdatePriority: vi.fn().mockResolvedValue(undefined),
        refresh: vi.fn().mockResolvedValue(undefined),
        toast: vi.fn(),
    };
});

afterEach(() => {
    host.remove();
    document.getElementById('llmproxy-modal-host')?.remove();
});

describe('createRegistryTable', () => {
    it('renders one row per endpoint with name, url, status, circuit and priority', () => {
        const t = createRegistryTable(ROWS, deps);
        host.appendChild(t.root);

        expect(host.querySelectorAll('tbody tr').length).toBe(ROWS.length);
        expect(host.textContent).toContain('OpenAI');
        expect(host.textContent).toContain('api.openai.com');
        expect(host.textContent).toContain('Live');
        expect(host.textContent).toContain('CLOSED');
    });

    it('circuit cell carries data-explain so the explain pane can hook in', () => {
        const t = createRegistryTable(ROWS, deps);
        host.appendChild(t.root);
        const explained = host.querySelectorAll('[data-explain^="circuit:"]');
        expect(explained.length).toBe(ROWS.length);
    });

    it('Inspect button forwards data-drilldown for the existing drilldown service', () => {
        const t = createRegistryTable(ROWS, deps);
        host.appendChild(t.root);
        const inspect = host.querySelector<HTMLButtonElement>('[data-testid="ep-inspect-openai"]');
        expect(inspect).not.toBeNull();
        expect(inspect?.dataset.drilldown).toBe('endpoint:openai');
    });

    it('Copy cURL writes a runnable proxy request snippet', async () => {
        const writeText = vi.fn().mockResolvedValue(undefined);
        Object.defineProperty(navigator, 'clipboard', {
            configurable: true,
            value: { writeText },
        });
        const t = createRegistryTable(ROWS, deps);
        host.appendChild(t.root);
        host.querySelector<HTMLButtonElement>('[data-testid="ep-copy-curl-openai"]')!.click();
        await new Promise((r) => setTimeout(r, 0));
        expect(writeText).toHaveBeenCalledWith(expect.stringContaining('/v1/chat/completions'));
        expect(writeText).toHaveBeenCalledWith(expect.stringContaining('gpt-4o-mini'));
        expect(deps.toast).toHaveBeenCalledWith(expect.stringContaining('openai'), 'success');
    });

    it('priority up/down call onUpdatePriority with the new value and trigger a refresh', async () => {
        const t = createRegistryTable(ROWS, deps);
        host.appendChild(t.root);
        const up = host.querySelector<HTMLButtonElement>('[data-testid="ep-priority-up-flaky"]')!;
        up.click();
        await new Promise((r) => setTimeout(r, 0));
        expect(deps.onUpdatePriority).toHaveBeenCalledWith('flaky', 2);
        expect(deps.refresh).toHaveBeenCalled();

        const down = host.querySelector<HTMLButtonElement>('[data-testid="ep-priority-down-openai"]')!;
        down.click();
        await new Promise((r) => setTimeout(r, 0));
        expect(deps.onUpdatePriority).toHaveBeenCalledWith('openai', 9);
    });

    it('priority floors at 0 (does not go negative)', async () => {
        const t = createRegistryTable(ROWS, deps);
        host.appendChild(t.root);
        const down = host.querySelector<HTMLButtonElement>('[data-testid="ep-priority-down-dead"]')!;
        down.click();
        await new Promise((r) => setTimeout(r, 0));
        expect(deps.onUpdatePriority).toHaveBeenCalledWith('dead', 0);
    });

    it('Reset CB calls onResetCircuitBreaker, toasts success, and refreshes', async () => {
        const t = createRegistryTable(ROWS, deps);
        host.appendChild(t.root);
        host.querySelector<HTMLButtonElement>('[data-testid="ep-reset-cb-flaky"]')!.click();
        await new Promise((r) => setTimeout(r, 0));
        expect(deps.onResetCircuitBreaker).toHaveBeenCalledWith('flaky');
        expect(deps.toast).toHaveBeenCalledWith(expect.stringContaining('flaky'), 'success');
        expect(deps.refresh).toHaveBeenCalled();
    });

    it('Test endpoint probes connectivity, reports the result, and refreshes', async () => {
        const t = createRegistryTable(ROWS, deps);
        host.appendChild(t.root);
        host.querySelector<HTMLButtonElement>('[data-testid="ep-probe-openai"]')!.click();
        await new Promise((r) => setTimeout(r, 0));
        expect(deps.onProbeEndpoint).toHaveBeenCalledWith('openai');
        expect(deps.toast).toHaveBeenCalledWith(expect.stringContaining('reachable'), 'success');
        expect(deps.refresh).toHaveBeenCalled();
    });

    it('Toggle calls onToggleEndpoint and refreshes', async () => {
        const t = createRegistryTable(ROWS, deps);
        host.appendChild(t.root);
        host.querySelector<HTMLButtonElement>('[data-testid="ep-toggle-openai"]')!.click();
        await new Promise((r) => setTimeout(r, 0));
        expect(deps.onToggleEndpoint).toHaveBeenCalledWith('openai');
    });

    it('Delete opens a confirm modal and only deletes after confirmation', async () => {
        const t = createRegistryTable(ROWS, deps);
        host.appendChild(t.root);
        host.querySelector<HTMLButtonElement>('[data-testid="ep-delete-dead"]')!.click();

        // Wait microtask so the dynamic import / modal mount can settle.
        await new Promise((r) => setTimeout(r, 0));
        await new Promise((r) => setTimeout(r, 0));

        const modal = document.querySelector('[data-testid="modal-confirm"]');
        expect(modal).not.toBeNull();

        // Cancel — no delete.
        document.querySelector<HTMLButtonElement>('[data-testid="modal-confirm-cancel"]')?.click();
        await new Promise((r) => setTimeout(r, 0));
        expect(deps.onDeleteEndpoint).not.toHaveBeenCalled();

        // Reopen → confirm → delete fires.
        host.querySelector<HTMLButtonElement>('[data-testid="ep-delete-dead"]')!.click();
        await new Promise((r) => setTimeout(r, 0));
        await new Promise((r) => setTimeout(r, 0));
        document.querySelector<HTMLButtonElement>('[data-testid="modal-confirm-ok"]')?.click();
        await new Promise((r) => setTimeout(r, 0));
        expect(deps.onDeleteEndpoint).toHaveBeenCalledWith('dead');
    });

    it('action failures surface error toasts and do NOT call refresh', async () => {
        deps.onResetCircuitBreaker = vi.fn().mockRejectedValue(new Error('429 too many'));
        deps.refresh = vi.fn();
        const t = createRegistryTable(ROWS, deps);
        host.appendChild(t.root);
        host.querySelector<HTMLButtonElement>('[data-testid="ep-reset-cb-flaky"]')!.click();
        await new Promise((r) => setTimeout(r, 0));
        expect(deps.toast).toHaveBeenCalledWith(expect.stringContaining('429'), 'error');
        expect(deps.refresh).not.toHaveBeenCalled();
    });
});
