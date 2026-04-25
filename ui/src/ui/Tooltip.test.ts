import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { attachTooltip } from './Tooltip';

let target: HTMLElement;

beforeEach(() => {
    vi.useFakeTimers();
    target = document.createElement('button');
    target.textContent = 'hover me';
    document.body.appendChild(target);
});

afterEach(() => {
    vi.useRealTimers();
    target.remove();
    document.getElementById('llmproxy-tooltip-host')?.remove();
});

describe('attachTooltip', () => {
    it('sets aria-describedby on the target', () => {
        attachTooltip(target, { content: 'Click to inspect' });
        expect(target.getAttribute('aria-describedby')).toMatch(/^tip-\d+$/);
    });

    it('hover after the delay shows a role=tooltip popover with the content', () => {
        attachTooltip(target, { content: 'Click to inspect', delay: 100 });
        target.dispatchEvent(new MouseEvent('mouseenter'));
        // Before the delay, no popover.
        expect(document.querySelector('[role="tooltip"]')).toBeNull();
        vi.advanceTimersByTime(100);
        const tip = document.querySelector('[role="tooltip"]');
        expect(tip).not.toBeNull();
        expect(tip?.textContent).toBe('Click to inspect');
    });

    it('mouseleave hides the tooltip', () => {
        attachTooltip(target, { content: 'X', delay: 50 });
        target.dispatchEvent(new MouseEvent('mouseenter'));
        vi.advanceTimersByTime(50);
        expect(document.querySelector('[role="tooltip"]')).not.toBeNull();
        target.dispatchEvent(new MouseEvent('mouseleave'));
        expect(document.querySelector('[role="tooltip"]')).toBeNull();
    });

    it('focus shows immediately (skips the hover delay)', () => {
        attachTooltip(target, { content: 'F', delay: 1_000 });
        target.dispatchEvent(new FocusEvent('focus'));
        // No timer advance; immediate.
        expect(document.querySelector('[role="tooltip"]')).not.toBeNull();
    });

    it('blur hides the tooltip', () => {
        attachTooltip(target, { content: 'B' });
        target.dispatchEvent(new FocusEvent('focus'));
        target.dispatchEvent(new FocusEvent('blur'));
        expect(document.querySelector('[role="tooltip"]')).toBeNull();
    });

    it('destroy() removes listeners and cleans up the visible popover', () => {
        const destroy = attachTooltip(target, { content: 'D', delay: 0 });
        target.dispatchEvent(new MouseEvent('mouseenter'));
        vi.advanceTimersByTime(0);
        expect(document.querySelector('[role="tooltip"]')).not.toBeNull();
        destroy();
        expect(document.querySelector('[role="tooltip"]')).toBeNull();
        expect(target.getAttribute('aria-describedby')).toBeNull();

        // After destroy, hover does NOT show a tooltip.
        target.dispatchEvent(new MouseEvent('mouseenter'));
        vi.advanceTimersByTime(1_000);
        expect(document.querySelector('[role="tooltip"]')).toBeNull();
    });

    it('danger intent flips the surface palette', () => {
        attachTooltip(target, { content: 'oops', intent: 'danger', delay: 0 });
        target.dispatchEvent(new MouseEvent('mouseenter'));
        vi.advanceTimersByTime(0);
        const tip = document.querySelector('[role="tooltip"]');
        expect(tip?.className).toContain('bg-rose-500/15');
    });
});
