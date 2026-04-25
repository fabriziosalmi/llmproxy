/**
 * Tabs primitive — multi-pane navigation with arrow-key support.
 *
 * Used by the drilldown drawer ("overview / timeline / config / related /
 * actions") and ad-hoc anywhere in the views. Tab labels can carry an
 * optional badge intent (e.g. unread count).
 *
 * Implementation notes:
 * - role=tablist + role=tab + role=tabpanel with proper aria-controls /
 *   aria-labelledby cross-links.
 * - Left/Right arrows (and Home/End) walk the tab list.
 * - Pane render functions are called lazily on first activation, so heavy
 *   panes don't pay their cost up front.
 */
import { cx } from './classnames';
import { createBadge, type BadgeIntent } from './Badge';

export interface TabSpec {
    id: string;
    label: string;
    /** Optional inline badge — useful for unread counts, error tags, etc. */
    badge?: { label: string; intent?: BadgeIntent };
    /** Lazy renderer for the pane content. Called once on first activation. */
    render: () => HTMLElement;
}

export interface TabsOptions {
    tabs: TabSpec[];
    initialTab?: string;
    onChange?: (id: string) => void;
    className?: string;
    testId?: string;
}

export interface TabsHandle {
    root: HTMLElement;
    setActive(id: string): void;
    getActive(): string;
}

export function createTabs(opts: TabsOptions): TabsHandle {
    if (!opts.tabs.length) throw new Error('createTabs requires at least one tab');

    const root = document.createElement('div');
    root.className = cx('flex flex-col gap-3', opts.className);
    if (opts.testId) root.setAttribute('data-testid', opts.testId);

    const list = document.createElement('div');
    list.setAttribute('role', 'tablist');
    // H.1: long tab labels overflow on narrow viewports — let the tablist
    // scroll horizontally rather than clipping or breaking the layout.
    list.className = 'flex items-center gap-1 border-b border-white/[0.06] overflow-x-auto scrollbar-none';
    root.appendChild(list);

    const panels = document.createElement('div');
    panels.className = 'min-h-0';
    root.appendChild(panels);

    const tabButtons: Map<string, HTMLButtonElement> = new Map();
    const panelEls: Map<string, HTMLElement> = new Map();
    const renderedPanes = new Set<string>();
    let activeId =
        opts.initialTab && opts.tabs.some((t) => t.id === opts.initialTab) ? opts.initialTab : opts.tabs[0]!.id;

    for (const tab of opts.tabs) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.id = `tab-${tab.id}`;
        btn.setAttribute('role', 'tab');
        btn.setAttribute('aria-controls', `tabpanel-${tab.id}`);
        btn.setAttribute('data-testid', `tab-${tab.id}`);
        btn.className = cx(
            'inline-flex shrink-0 items-center gap-2 px-3 py-2 text-[11px] font-semibold border-b-2 -mb-px',
            'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500/40',
            'transition-colors'
        );
        const labelSpan = document.createElement('span');
        labelSpan.textContent = tab.label;
        btn.appendChild(labelSpan);

        if (tab.badge) {
            btn.appendChild(createBadge({ label: tab.badge.label, intent: tab.badge.intent ?? 'neutral', size: 'sm' }));
        }

        btn.addEventListener('click', () => activate(tab.id));
        btn.addEventListener('keydown', onKeyNav);
        list.appendChild(btn);
        tabButtons.set(tab.id, btn);

        const panel = document.createElement('div');
        panel.id = `tabpanel-${tab.id}`;
        panel.setAttribute('role', 'tabpanel');
        panel.setAttribute('aria-labelledby', `tab-${tab.id}`);
        panel.setAttribute('data-testid', `tabpanel-${tab.id}`);
        panel.className = 'pt-2';
        panels.appendChild(panel);
        panelEls.set(tab.id, panel);
    }

    function paint(): void {
        for (const tab of opts.tabs) {
            const btn = tabButtons.get(tab.id)!;
            const panel = panelEls.get(tab.id)!;
            const isActive = tab.id === activeId;
            btn.setAttribute('aria-selected', String(isActive));
            btn.tabIndex = isActive ? 0 : -1;
            btn.classList.toggle('text-white', isActive);
            btn.classList.toggle('text-slate-500', !isActive);
            btn.classList.toggle('border-cyan-500/60', isActive);
            btn.classList.toggle('border-transparent', !isActive);
            btn.classList.toggle('hover:text-slate-200', !isActive);
            panel.hidden = !isActive;
            if (isActive && !renderedPanes.has(tab.id)) {
                panel.replaceChildren(tab.render());
                renderedPanes.add(tab.id);
            }
        }
    }

    function activate(id: string, focusBtn = false): void {
        if (!tabButtons.has(id) || activeId === id) {
            if (focusBtn) tabButtons.get(id)?.focus();
            return;
        }
        activeId = id;
        paint();
        if (focusBtn) tabButtons.get(id)?.focus();
        opts.onChange?.(id);
    }

    function onKeyNav(e: KeyboardEvent): void {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return;
        e.preventDefault();
        const ids = opts.tabs.map((t) => t.id);
        const cur = ids.indexOf(activeId);
        let next = cur;
        if (e.key === 'ArrowLeft') next = (cur - 1 + ids.length) % ids.length;
        else if (e.key === 'ArrowRight') next = (cur + 1) % ids.length;
        else if (e.key === 'Home') next = 0;
        else if (e.key === 'End') next = ids.length - 1;
        activate(ids[next]!, true);
    }

    paint();

    return {
        root,
        setActive(id: string): void {
            activate(id);
        },
        getActive(): string {
            return activeId;
        },
    };
}
