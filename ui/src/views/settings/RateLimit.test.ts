import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mountRateLimit, type RateLimitConfig } from './RateLimit';

const _normal: RateLimitConfig = {
    enabled: true,
    preset: 'normal',
    requests_per_minute: 60,
    burst: 10,
    presets: {
        strict: { requests_per_minute: 30, burst: 5 },
        normal: { requests_per_minute: 60, burst: 10 },
        relaxed: { requests_per_minute: 240, burst: 60 },
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

describe('mountRateLimit', () => {
    it('renders live numbers + the three preset buttons', async () => {
        const handle = mountRateLimit(host, {
            fetchRateLimitConfig: vi.fn().mockResolvedValue(_normal),
            setRateLimitPreset: vi.fn(),
        });
        await handle.refresh();

        expect(host.querySelector('[data-testid="rate-limit-rpm"]')?.textContent).toContain('60');
        expect(host.querySelector('[data-testid="rate-limit-burst"]')?.textContent).toContain('10');
        expect(host.querySelector('[data-testid="rate-limit-preset-current"]')?.textContent).toContain('normal');
        // Three buttons mounted, one per preset
        expect(host.querySelector('[data-testid="rate-limit-preset-strict"]')).not.toBeNull();
        expect(host.querySelector('[data-testid="rate-limit-preset-normal"]')).not.toBeNull();
        expect(host.querySelector('[data-testid="rate-limit-preset-relaxed"]')).not.toBeNull();
    });

    it('clicking a non-active preset POSTs setRateLimitPreset and toasts', async () => {
        // Keep fetchCfg returning the same shape on every call — the contract
        // we care about here is "click → POST + toast", not the post-click
        // re-render content (that's the responsibility of the next test).
        const setPreset = vi.fn().mockResolvedValue({ preset: 'strict', requests_per_minute: 30, burst: 5 });
        const fetchCfg = vi.fn().mockResolvedValue(_normal);
        const toast = vi.fn();

        const handle = mountRateLimit(host, { fetchRateLimitConfig: fetchCfg, setRateLimitPreset: setPreset }, toast);
        await handle.refresh();

        const strictBtn = host.querySelector<HTMLButtonElement>('[data-testid="rate-limit-preset-strict"]')!;
        strictBtn.click();
        // Wait for setPreset + the trailing refresh.
        await new Promise((r) => setTimeout(r, 0));
        await new Promise((r) => setTimeout(r, 0));

        expect(setPreset).toHaveBeenCalledWith('strict');
        expect(toast).toHaveBeenCalledWith('Rate limit preset → strict', 'success');
    });

    it('after a successful preset switch, the live numbers reflect the new preset', async () => {
        const setPreset = vi.fn().mockResolvedValue({ preset: 'strict', requests_per_minute: 30, burst: 5 });
        // First two calls return normal (mount + initial refresh), call 3
        // (post-click) returns strict.
        const fetchCfg = vi
            .fn()
            .mockResolvedValueOnce(_normal)
            .mockResolvedValueOnce(_normal)
            .mockResolvedValue({ ..._normal, preset: 'strict', requests_per_minute: 30, burst: 5 });

        const handle = mountRateLimit(host, { fetchRateLimitConfig: fetchCfg, setRateLimitPreset: setPreset });
        await handle.refresh();

        const strictBtn = host.querySelector<HTMLButtonElement>('[data-testid="rate-limit-preset-strict"]')!;
        strictBtn.click();
        // setPreset awaited then a refresh — give both ticks.
        await new Promise((r) => setTimeout(r, 0));
        await new Promise((r) => setTimeout(r, 0));

        expect(host.querySelector('[data-testid="rate-limit-rpm"]')?.textContent).toContain('30');
        expect(host.querySelector('[data-testid="rate-limit-preset-current"]')?.textContent).toContain('strict');
    });

    it('clicking the active preset is a no-op (no API call)', async () => {
        const setPreset = vi.fn();
        const handle = mountRateLimit(host, {
            fetchRateLimitConfig: vi.fn().mockResolvedValue(_normal),
            setRateLimitPreset: setPreset,
        });
        await handle.refresh();

        const normalBtn = host.querySelector<HTMLButtonElement>('[data-testid="rate-limit-preset-normal"]')!;
        normalBtn.click();
        await new Promise((r) => setTimeout(r, 0));
        expect(setPreset).not.toHaveBeenCalled();
    });

    it('a failing setRateLimitPreset surfaces an error toast and leaves the picker live', async () => {
        const setPreset = vi.fn().mockRejectedValue(new Error('500 backend down'));
        const toast = vi.fn();
        const handle = mountRateLimit(host, {
            fetchRateLimitConfig: vi.fn().mockResolvedValue(_normal),
            setRateLimitPreset: setPreset,
        }, toast);
        await handle.refresh();

        const strictBtn = host.querySelector<HTMLButtonElement>('[data-testid="rate-limit-preset-strict"]')!;
        strictBtn.click();
        await new Promise((r) => setTimeout(r, 0));

        expect(toast).toHaveBeenCalledWith(expect.stringContaining('Failed'), 'error');
        // Button is re-enabled after the error.
        expect(strictBtn.disabled).toBe(false);
    });

    it('shows the error state when the GET fails', async () => {
        const handle = mountRateLimit(host, {
            fetchRateLimitConfig: vi.fn().mockRejectedValue(new Error('boom')),
            setRateLimitPreset: vi.fn(),
        });
        await handle.refresh();
        expect(host.querySelector('[data-testid="rate-limit-error"]')).not.toBeNull();
        expect(host.textContent).toContain('boom');
    });

    it('renders preset=null as "custom" with the explanatory copy', async () => {
        const handle = mountRateLimit(host, {
            fetchRateLimitConfig: vi.fn().mockResolvedValue({ ..._normal, preset: null }),
            setRateLimitPreset: vi.fn(),
        });
        await handle.refresh();
        expect(host.querySelector('[data-testid="rate-limit-preset-current"]')?.textContent).toContain('custom');
        expect(host.textContent).toContain('config.yaml');
    });
});
