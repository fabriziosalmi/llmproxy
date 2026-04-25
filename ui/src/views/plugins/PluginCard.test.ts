import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createPluginCard } from './PluginCard';
import type { Plugin, PluginStats } from './types';

const PLUGIN: Plugin = {
    name: 'pii_masker',
    hook: 'pre_flight',
    entrypoint: 'plugins.ring2.pii_masker:PIIMasker',
    description: 'Mask emails, phones, SSNs, credit cards.',
    enabled: true,
    timeout_ms: 250,
    fail_policy: 'open',
    version: '1.4.0',
};

const STATS: PluginStats = {
    invocations: 1234,
    errors: 12,
    blocks: 5,
    avg_latency_ms: 1.7,
    latency_percentiles: { p50: 1.2, p95: 4.1, p99: 9.8 },
};

let host: HTMLElement;
let deps: {
    onToggle: ReturnType<typeof vi.fn>;
    onUninstall: ReturnType<typeof vi.fn>;
    refresh: ReturnType<typeof vi.fn>;
    toast: ReturnType<typeof vi.fn>;
};

beforeEach(() => {
    host = document.createElement('div');
    document.body.appendChild(host);
    deps = {
        onToggle: vi.fn().mockResolvedValue(undefined),
        onUninstall: vi.fn().mockResolvedValue(undefined),
        refresh: vi.fn().mockResolvedValue(undefined),
        toast: vi.fn(),
    };
});

afterEach(() => {
    host.remove();
    document.getElementById('llmproxy-modal-host')?.remove();
});

describe('createPluginCard', () => {
    it('renders the name, ring badge, timeout, fail policy and version', () => {
        const card = createPluginCard(PLUGIN, STATS, deps);
        host.appendChild(card);
        expect(host.textContent).toContain('pii_masker');
        expect(host.textContent).toContain('Mask emails');
        expect(host.textContent).toContain('250ms');
        expect(host.textContent).toContain('open');
        expect(host.textContent).toContain('v1.4.0');

        const ring = host.querySelector('[data-testid="plugin-ring-pii_masker"]');
        expect(ring?.textContent).toContain('PRE-FLIGHT');
    });

    it('renders the four-stat row with the right tones', () => {
        const card = createPluginCard(PLUGIN, STATS, deps);
        host.appendChild(card);
        const stats = host.querySelector('[data-testid="plugin-stats-pii_masker"]')!;
        expect(stats.textContent).toContain('1,234');
        expect(stats.textContent).toContain('5');
        // Error rate ~0.97% → printed with one decimal.
        expect(stats.textContent).toContain('1.0%');
        expect(stats.textContent).toContain('1.7');
    });

    it('shows the latency P50/P95/P99 row when percentiles are available', () => {
        const card = createPluginCard(PLUGIN, STATS, deps);
        host.appendChild(card);
        expect(host.textContent).toContain('P50 1.2');
        expect(host.textContent).toContain('P95 4.1');
        expect(host.textContent).toContain('P99 9.8');
    });

    it('hides the latency row when no percentiles are available', () => {
        const card = createPluginCard(PLUGIN, { invocations: 0 }, deps);
        host.appendChild(card);
        expect(host.textContent).not.toContain('P50');
    });

    it('Inspect button forwards data-drilldown for the existing service', () => {
        const card = createPluginCard(PLUGIN, STATS, deps);
        host.appendChild(card);
        const inspect = host.querySelector<HTMLElement>('[data-testid="plugin-inspect-pii_masker"]');
        expect(inspect?.dataset.drilldown).toBe('plugin:pii_masker');
    });

    it('Toggle button label flips with enabled state', () => {
        const enabled = createPluginCard(PLUGIN, STATS, deps);
        host.appendChild(enabled);
        expect(host.querySelector('[data-testid="plugin-toggle-pii_masker"]')?.textContent).toContain('Disable');

        host.replaceChildren();
        const disabled = createPluginCard({ ...PLUGIN, enabled: false }, STATS, deps);
        host.appendChild(disabled);
        expect(host.querySelector('[data-testid="plugin-toggle-pii_masker"]')?.textContent).toContain('Enable');
    });

    it('Toggle calls onToggle with the next state, fires toast and refresh', async () => {
        const card = createPluginCard(PLUGIN, STATS, deps);
        host.appendChild(card);
        host.querySelector<HTMLButtonElement>('[data-testid="plugin-toggle-pii_masker"]')!.click();
        await new Promise((r) => setTimeout(r, 0));
        expect(deps.onToggle).toHaveBeenCalledWith('pii_masker', false);
        expect(deps.toast).toHaveBeenCalledWith(expect.stringContaining('disabled'), 'success');
        expect(deps.refresh).toHaveBeenCalled();
    });

    it('Uninstall opens a confirm modal; cancel skips the API call', async () => {
        const card = createPluginCard(PLUGIN, STATS, deps);
        host.appendChild(card);
        host.querySelector<HTMLButtonElement>('[data-testid="plugin-uninstall-pii_masker"]')!.click();
        // Wait for the dynamic confirm import to settle.
        await new Promise((r) => setTimeout(r, 0));
        await new Promise((r) => setTimeout(r, 0));
        expect(document.querySelector('[data-testid="modal-confirm"]')).not.toBeNull();
        document.querySelector<HTMLButtonElement>('[data-testid="modal-confirm-cancel"]')?.click();
        await new Promise((r) => setTimeout(r, 0));
        expect(deps.onUninstall).not.toHaveBeenCalled();
    });

    it('Uninstall confirm fires the API call and refreshes', async () => {
        const card = createPluginCard(PLUGIN, STATS, deps);
        host.appendChild(card);
        host.querySelector<HTMLButtonElement>('[data-testid="plugin-uninstall-pii_masker"]')!.click();
        await new Promise((r) => setTimeout(r, 0));
        await new Promise((r) => setTimeout(r, 0));
        document.querySelector<HTMLButtonElement>('[data-testid="modal-confirm-ok"]')?.click();
        await new Promise((r) => setTimeout(r, 0));
        expect(deps.onUninstall).toHaveBeenCalledWith('pii_masker');
        expect(deps.refresh).toHaveBeenCalled();
    });

    it('renders the read-only config block when ui_schema is present', () => {
        const withSchema: Plugin = {
            ...PLUGIN,
            ui_schema: [
                { key: 'redact_iban', label: 'Redact IBAN', default: true },
                { key: 'mask_char', label: 'Mask char', default: '*' },
            ],
        };
        const card = createPluginCard(withSchema, STATS, deps);
        host.appendChild(card);
        expect(host.textContent).toContain('Redact IBAN');
        expect(host.textContent).toContain('Mask char');
    });
});
