import { describe, expect, it, vi } from 'vitest';
import { createEmptyState } from './EmptyState';

describe('EmptyState', () => {
    it('renders title, role=status and dashed surface', () => {
        const el = createEmptyState({ title: 'No threats yet' });
        expect(el.getAttribute('role')).toBe('status');
        expect(el.className).toContain('border-dashed');
        expect(el.querySelector('h3')?.textContent).toBe('No threats yet');
    });

    it('renders description and primary action', () => {
        const onClick = vi.fn();
        const el = createEmptyState({
            title: 'No endpoints',
            description: 'Add a provider to get started.',
            action: { label: 'Add endpoint', onClick },
        });
        expect(el.querySelector('p')?.textContent).toContain('Add a provider');

        const btn = el.querySelector('button');
        expect(btn).not.toBeNull();
        btn?.click();
        expect(onClick).toHaveBeenCalledTimes(1);
    });

    it('renders both primary and secondary actions when provided', () => {
        const el = createEmptyState({
            title: 'No data',
            action: { label: 'Try again' },
            secondaryAction: { label: 'View docs' },
        });
        const btns = el.querySelectorAll('button');
        expect(btns).toHaveLength(2);
        expect(btns[0]?.textContent).toContain('Try again');
        expect(btns[1]?.textContent).toContain('View docs');
    });
});
