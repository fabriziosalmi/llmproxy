import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mountGuardsGrid } from './Grid';
import { GUARDS } from './catalog';
import type { GuardsState } from './types';

let container: HTMLElement;

beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
});

afterEach(() => {
    container.remove();
});

const STATE: GuardsState = {
    features: { injection_guard: true, language_guard: false, link_sanitizer: true },
    proxyEnabled: true,
    priorityMode: false,
    firewall: { enabled: true, disabled_reason: null },
};

describe('mountGuardsGrid', () => {
    it('renders a card per catalog entry once a state is provided', () => {
        const handle = mountGuardsGrid(container, STATE, { toggleGuard: vi.fn() });
        const cards = container.querySelectorAll('[data-testid^="guard-card-"]');
        expect(cards.length).toBe(GUARDS.length);
        // Smoke-test that the firewall card surfaces the live status.
        expect(container.textContent).toContain('ASGI Firewall');
        handle.setState(STATE); // idempotent
    });

    it('shows skeletons while loading (initial state = null)', () => {
        mountGuardsGrid(container, null, { toggleGuard: vi.fn() });
        const skeletons = container.querySelectorAll('span[role="status"], span[aria-hidden="true"]');
        expect(skeletons.length).toBeGreaterThan(0);
        // No real cards yet.
        expect(container.querySelectorAll('[data-testid^="guard-card-"]').length).toBe(0);
    });

    it('setError shows the ErrorState with retry; retry returns to the loading state', () => {
        const handle = mountGuardsGrid(container, STATE, { toggleGuard: vi.fn() });
        handle.setError('Backend unreachable.', 'ECONNREFUSED');
        const err = container.querySelector('[data-testid="guards-grid-error"]')!;
        expect(err).not.toBeNull();
        const retry = err.querySelector<HTMLButtonElement>('[data-testid="error-state-retry"]')!;
        expect(retry).not.toBeNull();
        retry.click();
        // After retry → grid clears and shows skeletons (loading).
        expect(container.querySelector('[data-testid="guards-grid-error"]')).toBeNull();
    });

    it('firewall card surfaces "OFF · <reason>" when state.firewall.enabled=false', () => {
        const handle = mountGuardsGrid(container, STATE, { toggleGuard: vi.fn() });
        handle.setState({
            ...STATE,
            firewall: { enabled: false, disabled_reason: 'env:LLM_PROXY_FIREWALL_ENABLED' },
        });
        const fw = container.querySelector('[data-testid="guard-status-firewall"]');
        expect(fw?.textContent).toContain('OFF · env:LLM_PROXY_FIREWALL_ENABLED');
    });

    it('toggling a guard calls the dep, updates state, and emits a toast', async () => {
        // N.3: guard toggle is debounced 200 ms, so we need to advance fake
        // timers past the window before the API call fires.
        vi.useFakeTimers();
        const toggleGuard = vi.fn().mockResolvedValue({ enabled: true });
        const toast = vi.fn();
        mountGuardsGrid(container, STATE, { toggleGuard, toast });

        const sw = container.querySelector<HTMLButtonElement>('[data-testid="guard-toggle-language_guard"]')!;
        expect(sw.getAttribute('aria-checked')).toBe('false');
        sw.click();

        // Drain the debounce window, then any micro-tasks the resolved promise leaves.
        await vi.advanceTimersByTimeAsync(200);
        vi.useRealTimers();
        await new Promise((r) => setTimeout(r, 0));

        expect(toggleGuard).toHaveBeenCalledWith('language_guard', true);
        const fresh = container.querySelector<HTMLButtonElement>('[data-testid="guard-toggle-language_guard"]')!;
        expect(fresh.getAttribute('aria-checked')).toBe('true');
        expect(toast).toHaveBeenCalledWith(expect.stringContaining('Language Guard enabled'), 'success');
    });

    it('a failing toggle reverts the UI and shows an error toast', async () => {
        vi.useFakeTimers();
        const toggleGuard = vi.fn().mockRejectedValue(new Error('500 backend down'));
        const toast = vi.fn();
        mountGuardsGrid(container, STATE, { toggleGuard, toast });

        const sw = container.querySelector<HTMLButtonElement>('[data-testid="guard-toggle-injection_guard"]')!;
        expect(sw.getAttribute('aria-checked')).toBe('true');
        sw.click();
        await vi.advanceTimersByTimeAsync(200);
        vi.useRealTimers();
        await new Promise((r) => setTimeout(r, 0));

        const fresh = container.querySelector<HTMLButtonElement>('[data-testid="guard-toggle-injection_guard"]')!;
        // State did NOT change — toggle reverted.
        expect(fresh.getAttribute('aria-checked')).toBe('true');
        expect(toast).toHaveBeenCalledWith(expect.stringContaining('toggle failed'), 'error');
    });
});
