import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createDrawer } from './Drawer';

beforeEach(() => {
    vi.useFakeTimers();
});

afterEach(() => {
    vi.runAllTimers();
    vi.useRealTimers();
    document.getElementById('llmproxy-drawer-host')?.remove();
});

describe('createDrawer', () => {
    it('mounts an aside with role=dialog + aria-labelledby', () => {
        const handle = createDrawer({ title: 'Endpoint · ollama-auto', body: 'init' });
        const aside = document.querySelector('aside[role="dialog"]');
        expect(aside).not.toBeNull();
        expect(aside?.getAttribute('aria-modal')).toBe('true');
        const labelId = aside?.getAttribute('aria-labelledby');
        expect(document.getElementById(labelId!)?.textContent).toBe('Endpoint · ollama-auto');
        handle.close();
    });

    it('setTitle updates the heading without reopening', () => {
        const handle = createDrawer({ title: 'A' });
        handle.setTitle('B');
        const labelId = document.querySelector('aside')?.getAttribute('aria-labelledby');
        expect(document.getElementById(labelId!)?.textContent).toBe('B');
        handle.close();
    });

    it('setBody replaces the body content and accepts strings or nodes', () => {
        const handle = createDrawer({ title: 'X', body: 'first' });
        const aside = document.querySelector('aside')!;
        expect(aside.textContent).toContain('first');

        const node = document.createElement('p');
        node.textContent = 'second';
        handle.setBody(node);
        expect(aside.textContent).toContain('second');
        expect(aside.textContent).not.toContain('first');
        handle.close();
    });

    it('opening a second drawer reuses the existing one (single-drawer model)', () => {
        const a = createDrawer({ title: 'first', body: 'one' });
        const b = createDrawer({ title: 'second', body: 'two' });
        // Same handle — calling open() while one is live just updates content.
        expect(b).toBe(a);
        const asides = document.querySelectorAll('aside');
        expect(asides).toHaveLength(1);
        const labelId = asides[0]?.getAttribute('aria-labelledby');
        expect(document.getElementById(labelId!)?.textContent).toBe('second');
        expect(asides[0]?.textContent).toContain('two');
        a.close();
    });

    it('Escape closes the drawer and fires onClose after the slide-out animation', () => {
        const onClose = vi.fn();
        const handle = createDrawer({ title: 'X', onClose });
        document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
        expect(handle.isOpen).toBe(false);
        // Animation takes 200ms — onClose fires after that.
        vi.advanceTimersByTime(200);
        expect(onClose).toHaveBeenCalledTimes(1);
        expect(document.querySelector('aside')).toBeNull();
    });

    it('the close button closes the drawer', () => {
        const handle = createDrawer({ title: 'X' });
        const close = document.querySelector<HTMLButtonElement>('[data-testid="drawer-close"]')!;
        close.click();
        expect(handle.isOpen).toBe(false);
        vi.advanceTimersByTime(200);
        expect(document.querySelector('aside')).toBeNull();
    });

    it('clicking the backdrop closes the drawer', () => {
        const handle = createDrawer({ title: 'X' });
        const backdrop = document.querySelector('#llmproxy-drawer-host > div') as HTMLElement;
        backdrop.click();
        expect(handle.isOpen).toBe(false);
        vi.advanceTimersByTime(200);
    });

    it('handle.close() is idempotent', () => {
        const onClose = vi.fn();
        const handle = createDrawer({ title: 'X', onClose });
        handle.close();
        handle.close();
        handle.close();
        vi.advanceTimersByTime(200);
        expect(onClose).toHaveBeenCalledTimes(1);
    });
});
