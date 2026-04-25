import { describe, expect, it, vi } from 'vitest';
import { createErrorState } from './ErrorState';

describe('ErrorState', () => {
    it('uses role=alert with aria-live for screen-reader announcements', () => {
        const el = createErrorState({ title: 'Something broke' });
        expect(el.getAttribute('role')).toBe('alert');
        expect(el.getAttribute('aria-live')).toBe('polite');
    });

    it('renders title, description and a collapsible detail block', () => {
        const el = createErrorState({
            title: 'Failed to load',
            description: 'Could not reach the proxy.',
            detail: 'TypeError: Failed to fetch',
        });
        expect(el.querySelector('h3')?.textContent).toBe('Failed to load');
        expect(el.querySelector('p')?.textContent).toContain('Could not reach');
        const pre = el.querySelector('pre');
        expect(pre?.textContent).toBe('TypeError: Failed to fetch');
    });

    it('wires the retry button and exposes a stable testid', () => {
        const onRetry = vi.fn();
        const el = createErrorState({ title: 'Boom', onRetry });
        const retry = el.querySelector('[data-testid="error-state-retry"]') as HTMLButtonElement;
        expect(retry).not.toBeNull();
        retry.click();
        expect(onRetry).toHaveBeenCalledTimes(1);
    });

    it('uses a custom retry label when provided', () => {
        const el = createErrorState({ title: 'X', onRetry: () => {}, retryLabel: 'Try again' });
        const retry = el.querySelector('[data-testid="error-state-retry"]') as HTMLButtonElement;
        expect(retry.textContent).toContain('Try again');
    });
});
