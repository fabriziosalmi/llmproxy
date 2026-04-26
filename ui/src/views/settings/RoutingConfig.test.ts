import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mountRoutingConfig, type RoutingConfig } from './RoutingConfig';

const _smart: RoutingConfig = { cost_weight: 0.3, priority_mode: false, strategy: 'smart_weighted' };
const _perf: RoutingConfig = { cost_weight: 0.0, priority_mode: false, strategy: 'performance' };
const _priority: RoutingConfig = { cost_weight: 0.3, priority_mode: true, strategy: 'priority' };

let host: HTMLElement;

beforeEach(() => {
    host = document.createElement('div');
    document.body.appendChild(host);
});
afterEach(() => {
    host.remove();
});

describe('mountRoutingConfig', () => {
    it('renders slider + value + 3 quick-set buttons + strategy badge', async () => {
        const handle = mountRoutingConfig(host, {
            fetchRoutingConfig: vi.fn().mockResolvedValue(_smart),
            setRoutingCostWeight: vi.fn(),
        });
        await handle.refresh();

        const slider = host.querySelector<HTMLInputElement>('[data-testid="cost-weight-slider"]')!;
        expect(slider.value).toBe('0.3');
        expect(host.querySelector('[data-testid="cost-weight-value"]')?.textContent).toBe('0.30');
        expect(host.querySelector('[data-testid="cost-weight-quickset-performance"]')).not.toBeNull();
        expect(host.querySelector('[data-testid="cost-weight-quickset-smart"]')).not.toBeNull();
        expect(host.querySelector('[data-testid="cost-weight-quickset-costfirst"]')).not.toBeNull();
        expect(host.querySelector('[data-testid="routing-strategy-badge"]')?.textContent).toContain('smart');
    });

    it('input event updates the visible big-number live (without POSTing)', async () => {
        const setW = vi.fn();
        const handle = mountRoutingConfig(host, {
            fetchRoutingConfig: vi.fn().mockResolvedValue(_smart),
            setRoutingCostWeight: setW,
        });
        await handle.refresh();

        const slider = host.querySelector<HTMLInputElement>('[data-testid="cost-weight-slider"]')!;
        slider.value = '0.7';
        slider.dispatchEvent(new Event('input'));
        expect(host.querySelector('[data-testid="cost-weight-value"]')?.textContent).toBe('0.70');
        expect(setW).not.toHaveBeenCalled(); // input only — no POST
    });

    it('change event POSTs setRoutingCostWeight + toasts + refetches', async () => {
        const setW = vi.fn().mockResolvedValue({ ..._smart, cost_weight: 0.7 });
        const fetchCfg = vi.fn().mockResolvedValue(_smart);
        const toast = vi.fn();
        const handle = mountRoutingConfig(host, { fetchRoutingConfig: fetchCfg, setRoutingCostWeight: setW }, toast);
        await handle.refresh();

        const slider = host.querySelector<HTMLInputElement>('[data-testid="cost-weight-slider"]')!;
        slider.value = '0.7';
        slider.dispatchEvent(new Event('change'));
        // Allow setW + the trailing refresh.
        await new Promise((r) => setTimeout(r, 0));
        await new Promise((r) => setTimeout(r, 0));

        expect(setW).toHaveBeenCalledWith(0.7);
        expect(toast).toHaveBeenCalledWith(expect.stringContaining('cost_weight → 0.70'), 'success');
    });

    it('clicking the active quick-set is a no-op', async () => {
        const setW = vi.fn();
        const handle = mountRoutingConfig(host, {
            fetchRoutingConfig: vi.fn().mockResolvedValue(_smart),
            setRoutingCostWeight: setW,
        });
        await handle.refresh();

        // Smart (0.3) matches the fixture — clicking it should not POST.
        const smartBtn = host.querySelector<HTMLButtonElement>('[data-testid="cost-weight-quickset-smart"]')!;
        smartBtn.click();
        await new Promise((r) => setTimeout(r, 0));
        expect(setW).not.toHaveBeenCalled();
    });

    it('clicking a non-active quick-set POSTs the preset value', async () => {
        const setW = vi.fn().mockResolvedValue({ ..._smart, cost_weight: 0.8 });
        const handle = mountRoutingConfig(host, {
            fetchRoutingConfig: vi.fn().mockResolvedValue(_smart),
            setRoutingCostWeight: setW,
        });
        await handle.refresh();

        const costFirstBtn = host.querySelector<HTMLButtonElement>('[data-testid="cost-weight-quickset-costfirst"]')!;
        costFirstBtn.click();
        await new Promise((r) => setTimeout(r, 0));
        await new Promise((r) => setTimeout(r, 0));

        expect(setW).toHaveBeenCalledWith(0.8);
    });

    it('priority_mode disables the slider + shows the priority notice', async () => {
        const setW = vi.fn();
        const handle = mountRoutingConfig(host, {
            fetchRoutingConfig: vi.fn().mockResolvedValue(_priority),
            setRoutingCostWeight: setW,
        });
        await handle.refresh();

        const slider = host.querySelector<HTMLInputElement>('[data-testid="cost-weight-slider"]')!;
        expect(slider.disabled).toBe(true);
        expect(host.textContent).toContain('Priority Steering is ON');
    });

    it('failed POST surfaces an error toast and reverts the slider value', async () => {
        const setW = vi.fn().mockRejectedValue(new Error('500 backend down'));
        const fetchCfg = vi.fn().mockResolvedValue(_smart);
        const toast = vi.fn();
        const handle = mountRoutingConfig(host, { fetchRoutingConfig: fetchCfg, setRoutingCostWeight: setW }, toast);
        await handle.refresh();

        const slider = host.querySelector<HTMLInputElement>('[data-testid="cost-weight-slider"]')!;
        slider.value = '0.9';
        slider.dispatchEvent(new Event('change'));
        await new Promise((r) => setTimeout(r, 0));
        await new Promise((r) => setTimeout(r, 0));

        expect(toast).toHaveBeenCalledWith(expect.stringContaining('Failed'), 'error');
        // Slider reverts to the persisted value.
        expect(slider.value).toBe('0.3');
        expect(host.querySelector('[data-testid="cost-weight-value"]')?.textContent).toBe('0.30');
    });

    it('renders an error state when the GET fails', async () => {
        const handle = mountRoutingConfig(host, {
            fetchRoutingConfig: vi.fn().mockRejectedValue(new Error('boom')),
            setRoutingCostWeight: vi.fn(),
        });
        await handle.refresh();
        expect(host.querySelector('[data-testid="routing-config-error"]')).not.toBeNull();
        expect(host.textContent).toContain('boom');
    });

    it('strategy=performance renders the perf description', async () => {
        const handle = mountRoutingConfig(host, {
            fetchRoutingConfig: vi.fn().mockResolvedValue(_perf),
            setRoutingCostWeight: vi.fn(),
        });
        await handle.refresh();
        expect(host.textContent).toContain('Ignore cost');
    });
});
