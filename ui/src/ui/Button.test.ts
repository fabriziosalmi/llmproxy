import { describe, expect, it, vi } from 'vitest';
import { createButton } from './Button';

describe('Button', () => {
    it('renders with the secondary variant by default', () => {
        const btn = createButton({ label: 'Save' });
        expect(btn.tagName).toBe('BUTTON');
        expect(btn.type).toBe('button');
        expect(btn.textContent).toContain('Save');
        expect(btn.className).toContain('bg-white/5');
    });

    it('applies the variant + size classes', () => {
        const btn = createButton({ label: 'Delete', variant: 'destructive', size: 'sm' });
        expect(btn.className).toContain('bg-red-500/15');
        expect(btn.className).toContain('h-7');
    });

    it('attaches the click handler', () => {
        const onClick = vi.fn();
        const btn = createButton({ label: 'Click me', onClick });
        btn.click();
        expect(onClick).toHaveBeenCalledTimes(1);
    });

    it('does not fire click when disabled', () => {
        const onClick = vi.fn();
        const btn = createButton({ label: 'Disabled', onClick, disabled: true });
        btn.click();
        expect(onClick).not.toHaveBeenCalled();
        expect(btn.disabled).toBe(true);
    });

    it('forwards aria-label and data-testid', () => {
        const btn = createButton({ label: '✕', ariaLabel: 'Close', testId: 'close-btn' });
        expect(btn.getAttribute('aria-label')).toBe('Close');
        expect(btn.getAttribute('data-testid')).toBe('close-btn');
    });

    it('reflects pressed state via aria-pressed', () => {
        const on = createButton({ label: 'Mute', pressed: true });
        const off = createButton({ label: 'Mute', pressed: false });
        expect(on.getAttribute('aria-pressed')).toBe('true');
        expect(off.getAttribute('aria-pressed')).toBe('false');
    });

    it('renders the leading icon as aria-hidden', () => {
        const btn = createButton({ label: 'Open', icon: '<svg data-icon="caret"></svg>' });
        const iconHost = btn.querySelector('[aria-hidden="true"]');
        expect(iconHost).not.toBeNull();
        expect(iconHost?.querySelector('svg')).not.toBeNull();
    });
});
