import { describe, expect, it, vi } from 'vitest';
import { createTabs, type TabSpec } from './Tabs';

function makeSpec(id: string, label: string, body: string): TabSpec {
    return {
        id,
        label,
        render: () => {
            const el = document.createElement('p');
            el.textContent = body;
            return el;
        },
    };
}

const TABS: TabSpec[] = [
    makeSpec('overview', 'Overview', 'overview-body'),
    makeSpec('timeline', 'Timeline', 'timeline-body'),
    makeSpec('config', 'Config', 'config-body'),
];

describe('createTabs', () => {
    it('renders tablist + 1 tabpanel per tab; first tab is active by default', () => {
        const t = createTabs({ tabs: TABS });
        expect(t.root.querySelector('[role="tablist"]')).not.toBeNull();
        const panels = t.root.querySelectorAll('[role="tabpanel"]');
        expect(panels).toHaveLength(3);
        expect(t.getActive()).toBe('overview');
        // Only the active panel has visible content.
        expect((panels[0] as HTMLElement).hidden).toBe(false);
        expect((panels[1] as HTMLElement).hidden).toBe(true);
    });

    it('initialTab respects the input', () => {
        const t = createTabs({ tabs: TABS, initialTab: 'config' });
        expect(t.getActive()).toBe('config');
    });

    it('clicking a tab activates it and lazy-renders its pane', () => {
        const renderTimeline = vi.fn(() => {
            const el = document.createElement('p');
            el.textContent = 'lazy';
            return el;
        });
        const tabs: TabSpec[] = [
            { id: 'a', label: 'A', render: () => document.createElement('p') },
            { id: 'b', label: 'B', render: renderTimeline },
        ];
        const t = createTabs({ tabs });
        // 'b' not yet rendered.
        expect(renderTimeline).not.toHaveBeenCalled();
        const btnB = t.root.querySelector<HTMLButtonElement>('[data-testid="tab-b"]')!;
        btnB.click();
        expect(renderTimeline).toHaveBeenCalledTimes(1);
        expect(t.getActive()).toBe('b');
        // Switching back to 'a' does NOT re-render 'b'.
        const btnA = t.root.querySelector<HTMLButtonElement>('[data-testid="tab-a"]')!;
        btnA.click();
        btnB.click();
        expect(renderTimeline).toHaveBeenCalledTimes(1);
    });

    it('aria-selected, tabIndex roving and panel hidden flip together', () => {
        const t = createTabs({ tabs: TABS });
        const overview = t.root.querySelector<HTMLButtonElement>('[data-testid="tab-overview"]')!;
        const timeline = t.root.querySelector<HTMLButtonElement>('[data-testid="tab-timeline"]')!;
        expect(overview.getAttribute('aria-selected')).toBe('true');
        expect(overview.tabIndex).toBe(0);
        expect(timeline.tabIndex).toBe(-1);
        timeline.click();
        expect(overview.getAttribute('aria-selected')).toBe('false');
        expect(overview.tabIndex).toBe(-1);
        expect(timeline.tabIndex).toBe(0);
    });

    it('arrow keys walk the tab list and Home/End jump to ends', () => {
        const t = createTabs({ tabs: TABS });
        const list = t.root.querySelector('[role="tablist"]')!;
        const overview = t.root.querySelector<HTMLButtonElement>('[data-testid="tab-overview"]')!;

        overview.focus();
        list.querySelector<HTMLButtonElement>('[aria-selected="true"]')!.dispatchEvent(
            new KeyboardEvent('keydown', { key: 'ArrowRight' })
        );
        expect(t.getActive()).toBe('timeline');

        list.querySelector<HTMLButtonElement>('[aria-selected="true"]')!.dispatchEvent(
            new KeyboardEvent('keydown', { key: 'End' })
        );
        expect(t.getActive()).toBe('config');

        list.querySelector<HTMLButtonElement>('[aria-selected="true"]')!.dispatchEvent(
            new KeyboardEvent('keydown', { key: 'Home' })
        );
        expect(t.getActive()).toBe('overview');

        list.querySelector<HTMLButtonElement>('[aria-selected="true"]')!.dispatchEvent(
            new KeyboardEvent('keydown', { key: 'ArrowLeft' })
        );
        expect(t.getActive()).toBe('config');
    });

    it('onChange fires on user activation but not on initial render', () => {
        const onChange = vi.fn();
        const t = createTabs({ tabs: TABS, onChange });
        expect(onChange).not.toHaveBeenCalled();
        const timeline = t.root.querySelector<HTMLButtonElement>('[data-testid="tab-timeline"]')!;
        timeline.click();
        expect(onChange).toHaveBeenCalledWith('timeline');
    });

    it('badge renders inline next to the tab label', () => {
        const tabs: TabSpec[] = [
            { id: 'a', label: 'A', render: () => document.createElement('p'), badge: { label: '7', intent: 'danger' } },
            { id: 'b', label: 'B', render: () => document.createElement('p') },
        ];
        const t = createTabs({ tabs });
        const btnA = t.root.querySelector<HTMLButtonElement>('[data-testid="tab-a"]')!;
        expect(btnA.textContent).toContain('7');
    });

    it('throws when constructed with no tabs', () => {
        expect(() => createTabs({ tabs: [] })).toThrowError(/at least one/);
    });
});
