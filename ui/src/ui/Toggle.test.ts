import { describe, expect, it, vi } from 'vitest';
import { createToggle } from './Toggle';

describe('createToggle', () => {
    it('renders role=switch with aria-checked and a visible label', () => {
        const t = createToggle({ label: 'Firewall' });
        const sw = t.root.querySelector<HTMLButtonElement>('[role="switch"]')!;
        expect(sw).not.toBeNull();
        expect(sw.getAttribute('aria-checked')).toBe('false');
        expect(sw.getAttribute('aria-label')).toBe('Firewall');
        expect(t.root.textContent).toContain('Firewall');
    });

    it('clicking flips state and fires onChange', () => {
        const onChange = vi.fn();
        const t = createToggle({ label: 'X', onChange });
        const sw = t.root.querySelector<HTMLButtonElement>('[role="switch"]')!;
        sw.click();
        expect(t.isChecked()).toBe(true);
        expect(onChange).toHaveBeenCalledWith(true);

        sw.click();
        expect(t.isChecked()).toBe(false);
        expect(onChange).toHaveBeenLastCalledWith(false);
    });

    it('Space and Enter also flip the state', () => {
        const onChange = vi.fn();
        const t = createToggle({ label: 'X', onChange });
        const sw = t.root.querySelector<HTMLButtonElement>('[role="switch"]')!;
        sw.dispatchEvent(new KeyboardEvent('keydown', { key: ' ' }));
        expect(onChange).toHaveBeenCalledTimes(1);
        sw.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
        expect(onChange).toHaveBeenCalledTimes(2);
    });

    it('disabled prevents click and keyboard from changing state', () => {
        const onChange = vi.fn();
        const t = createToggle({ label: 'X', disabled: true, onChange });
        const sw = t.root.querySelector<HTMLButtonElement>('[role="switch"]')!;
        sw.click();
        sw.dispatchEvent(new KeyboardEvent('keydown', { key: ' ' }));
        expect(onChange).not.toHaveBeenCalled();
        expect(t.isChecked()).toBe(false);
    });

    it('setChecked updates state without firing onChange unless requested', () => {
        const onChange = vi.fn();
        const t = createToggle({ label: 'X', onChange });
        t.setChecked(true);
        expect(t.isChecked()).toBe(true);
        expect(onChange).not.toHaveBeenCalled();

        t.setChecked(false, true);
        expect(t.isChecked()).toBe(false);
        expect(onChange).toHaveBeenCalledWith(false);
    });

    it('setDisabled flips the disabled attribute live', () => {
        const t = createToggle({ label: 'X' });
        const sw = t.root.querySelector<HTMLButtonElement>('[role="switch"]')!;
        expect(sw.hasAttribute('disabled')).toBe(false);
        t.setDisabled(true);
        expect(sw.hasAttribute('disabled')).toBe(true);
        t.setDisabled(false);
        expect(sw.hasAttribute('disabled')).toBe(false);
    });

    it('renders the optional description line', () => {
        const t = createToggle({ label: 'Firewall', description: 'WAF on Ring 0' });
        expect(t.root.textContent).toContain('WAF on Ring 0');
    });
});
