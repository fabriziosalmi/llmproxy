import { describe, expect, it, vi } from 'vitest';
import { createInput } from './Input';

describe('createInput', () => {
    it('renders label + input with htmlFor wiring', () => {
        const f = createInput({ name: 'ep-name', label: 'Name' });
        expect(f.root.querySelector('label')?.htmlFor).toBe('ep-name');
        expect(f.input.id).toBe('ep-name');
        expect(f.input.name).toBe('ep-name');
    });

    it('marks the label with * when required', () => {
        const f = createInput({ name: 'ep-name', label: 'Name', required: true });
        expect(f.root.querySelector('label')?.textContent).toBe('Name *');
        expect(f.input.required).toBe(true);
    });

    it('shows help text by default and hides it when an error is set', () => {
        const f = createInput({ name: 'x', label: 'X', helpText: 'Use letters and digits.' });
        const help = f.root.querySelectorAll('p')[0]!;
        expect(help.textContent).toContain('Use letters');
        expect(help.classList.contains('hidden')).toBe(false);

        f.setError('bad');
        expect(help.classList.contains('hidden')).toBe(true);
        const err = f.root.querySelector('[role="alert"]')!;
        expect(err.textContent).toBe('bad');
        expect(f.input.getAttribute('aria-invalid')).toBe('true');
    });

    it('aria-describedby includes the visible help/error ids', () => {
        const f = createInput({ name: 'x', label: 'X', helpText: 'help text' });
        expect(f.input.getAttribute('aria-describedby')).toBe('x-help');

        f.setError('fail');
        expect(f.input.getAttribute('aria-describedby')).toBe('x-err');

        f.setError(null);
        expect(f.input.getAttribute('aria-describedby')).toBe('x-help');
    });

    it('clearing the input on user typing also clears the error', () => {
        const onInput = vi.fn();
        const f = createInput({ name: 'x', label: 'X', error: 'invalid', onInput });
        expect(f.input.getAttribute('aria-invalid')).toBe('true');

        f.input.value = 'a';
        f.input.dispatchEvent(new Event('input'));
        expect(f.input.getAttribute('aria-invalid')).toBeNull();
        expect(onInput).toHaveBeenCalledWith('a', expect.any(Event));
    });

    it('exposes setValue / getValue', () => {
        const f = createInput({ name: 'x', label: 'X' });
        expect(f.getValue()).toBe('');
        f.setValue('hello');
        expect(f.getValue()).toBe('hello');
        expect(f.input.value).toBe('hello');
    });

    it('forwards type and autoComplete', () => {
        const f = createInput({ name: 'pwd', label: 'Pwd', type: 'password', autoComplete: 'new-password' });
        expect(f.input.type).toBe('password');
        expect(f.input.autocomplete).toBe('new-password');
    });

    it('fires onChange on the change event with the current value', () => {
        const onChange = vi.fn();
        const f = createInput({ name: 'x', label: 'X', onChange });
        f.input.value = 'q';
        f.input.dispatchEvent(new Event('change'));
        expect(onChange).toHaveBeenCalledWith('q', expect.any(Event));
    });
});
