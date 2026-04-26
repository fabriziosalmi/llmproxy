import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
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

    // N.3 — Trailing-debounce on onChange. Default 0 keeps the existing
    // sync behavior (covered above); >0 collapses rapid flips into one fire.
    describe('debounceMs', () => {
        beforeEach(() => vi.useFakeTimers());
        afterEach(() => vi.useRealTimers());

        it('10 rapid clicks fire onChange once with the final state', () => {
            const onChange = vi.fn();
            const t = createToggle({ label: 'X', onChange, debounceMs: 200 });
            const sw = t.root.querySelector<HTMLButtonElement>('[role="switch"]')!;

            for (let i = 0; i < 10; i++) sw.click();
            // No fire yet — within the debounce window.
            expect(onChange).not.toHaveBeenCalled();

            vi.advanceTimersByTime(200);
            expect(onChange).toHaveBeenCalledTimes(1);
            // 10 flips from initial false → final state is false (even count).
            expect(onChange).toHaveBeenCalledWith(false);
        });

        it('a flip + pause + flip fires twice (separate windows)', () => {
            const onChange = vi.fn();
            const t = createToggle({ label: 'X', onChange, debounceMs: 200 });
            const sw = t.root.querySelector<HTMLButtonElement>('[role="switch"]')!;

            sw.click();
            vi.advanceTimersByTime(250);
            expect(onChange).toHaveBeenCalledTimes(1);

            sw.click();
            vi.advanceTimersByTime(250);
            expect(onChange).toHaveBeenCalledTimes(2);
        });

        it('debounceMs=0 fires synchronously (default behavior preserved)', () => {
            const onChange = vi.fn();
            const t = createToggle({ label: 'X', onChange, debounceMs: 0 });
            const sw = t.root.querySelector<HTMLButtonElement>('[role="switch"]')!;
            sw.click();
            expect(onChange).toHaveBeenCalledTimes(1);
        });

        it('setChecked(.., fire=true) also honors the debounce', () => {
            const onChange = vi.fn();
            const t = createToggle({ label: 'X', onChange, debounceMs: 200 });
            t.setChecked(true, true);
            t.setChecked(false, true);
            t.setChecked(true, true);
            expect(onChange).not.toHaveBeenCalled();
            vi.advanceTimersByTime(200);
            expect(onChange).toHaveBeenCalledTimes(1);
            expect(onChange).toHaveBeenCalledWith(true);
        });
    });
});
