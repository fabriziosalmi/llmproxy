import { describe, expect, it, vi } from 'vitest';
import { createSelect } from './Select';

const OPTS = [
    { value: 'info', label: 'info' },
    { value: 'debug', label: 'debug' },
];

describe('createSelect', () => {
    it('renders label + select with htmlFor wiring and options', () => {
        const f = createSelect({ name: 'log-level', label: 'Log level', options: OPTS });
        expect(f.root.querySelector('label')?.htmlFor).toBe('log-level');
        expect(f.select.id).toBe('log-level');
        expect(f.select.querySelectorAll('option').length).toBe(2);
    });

    it('selects the provided value', () => {
        const f = createSelect({ name: 'x', label: 'X', options: OPTS, value: 'debug' });
        expect(f.getValue()).toBe('debug');
    });

    it('shows help text and hides it on error, toggling aria-invalid', () => {
        const f = createSelect({ name: 'x', label: 'X', options: OPTS, helpText: 'Pick one.' });
        const help = f.root.querySelectorAll('p')[0]!;
        expect(help.classList.contains('hidden')).toBe(false);
        f.setError('nope');
        expect(help.classList.contains('hidden')).toBe(true);
        expect(f.select.getAttribute('aria-invalid')).toBe('true');
        expect(f.root.querySelector('[role="alert"]')?.textContent).toBe('nope');
    });

    it('fires onChange and clears a prior error', () => {
        const onChange = vi.fn();
        const f = createSelect({ name: 'x', label: 'X', options: OPTS, onChange });
        f.setError('bad');
        f.select.value = 'debug';
        f.select.dispatchEvent(new Event('change'));
        expect(onChange).toHaveBeenCalledWith('debug', expect.any(Event));
        expect(f.select.getAttribute('aria-invalid')).toBeNull();
    });
});
