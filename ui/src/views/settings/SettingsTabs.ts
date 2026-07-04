/**
 * Settings sub-navigation — horizontal tabs that show ONE section at a time.
 *
 * Replaces the old sticky "navpill" jump-links + long scroll. The six Settings
 * `<section id="settings-sec-*">` blocks stay in the DOM and keep mounting into
 * their own hosts exactly as before (this controller does not touch how sections
 * are populated); it only toggles which one is visible, giving instant, scroll-
 * free navigation. ARIA tablist semantics, arrow/Home/End keyboard nav, and a
 * `#settings/<id>` hash for deep-linking (and Cmd+K) all live here.
 *
 * Kept separate from the generic `createTabs` primitive on purpose: that one
 * owns its panels and lazily renders them, which fights the existing "mount into
 * fixed host divs by id" flow. This controller drives pre-existing external
 * section elements instead.
 *
 * Deep-linking conforms to the app's own hash-router convention (services/
 * urlstate.js): the Settings sub-tab is the second path segment of the view
 * hash — `#/settings/<sub>` — so it composes with the top-level router in
 * content.js and its `?params` instead of being a parallel scheme.
 */
import { hashSub, setHashView } from '../../../services/urlstate.js';

export interface SettingsTabSpec {
    /** Stable id; the section element is `settings-sec-${id}`. */
    id: string;
    label: string;
}

export interface SettingsTabsOptions {
    /** Active tab when no valid hash is present. Defaults to the first tab. */
    initial?: string;
    onChange?: (id: string) => void;
    /** Where the section elements live (defaults to document). Injectable for tests. */
    root?: Document | HTMLElement;
    /** Read/write the deep-link hash. Defaults to true; off in tests. */
    useHash?: boolean;
    /**
     * Called before leaving `fromId` for `toId`. Return false (or a Promise of
     * false) to VETO the switch — used to confirm away from unsaved edits. The
     * hash is restored if a hash-triggered switch is vetoed.
     */
    guard?: (fromId: string, toId: string) => boolean | Promise<boolean>;
}

export interface TabBadge {
    /** Small count/label pill. Omit (with dot:true) for a bare status dot. */
    text?: string;
    intent?: 'warning' | 'danger' | 'info';
    /** Render a bare dot instead of a text pill (e.g. "unsaved"). */
    dot?: boolean;
}

export interface SettingsTabsHandle {
    root: HTMLElement;
    setActive(id: string): void;
    getActive(): string;
    /** Set or clear a live badge on a tab (unsaved dot, warning count, …). */
    setBadge(id: string, badge: TabBadge | null): void;
    /** Detach listeners (hashchange). */
    destroy(): void;
}

const BADGE_TONE: Record<NonNullable<TabBadge['intent']>, string> = {
    warning: 'text-amber-400 bg-amber-400/15',
    danger: 'text-rose-400 bg-rose-400/15',
    info: 'text-cyan-300 bg-cyan-400/15',
};
const DOT_COLOR: Record<NonNullable<TabBadge['intent']>, string> = {
    warning: 'bg-amber-400',
    danger: 'bg-rose-400',
    info: 'bg-cyan-400',
};

function tabBtnClass(active: boolean): string {
    return [
        'inline-flex shrink-0 items-center gap-2 px-3.5 py-2 text-[11px] font-semibold border-b-2 -mb-px',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500/40 rounded-t transition-colors',
        active
            ? 'text-white border-cyan-500/60'
            : 'text-slate-500 border-transparent hover:text-slate-200 hover:border-white/10',
    ].join(' ');
}

export function mountSettingsTabs(
    tabbarHost: HTMLElement,
    tabs: SettingsTabSpec[],
    opts: SettingsTabsOptions = {}
): SettingsTabsHandle {
    const root = opts.root ?? document;
    const useHash = opts.useHash ?? true;
    if (!tabs.length) throw new Error('mountSettingsTabs requires at least one tab');

    const sectionOf = (id: string): HTMLElement | null =>
        root.querySelector<HTMLElement>(`#settings-sec-${id}`);

    const list = document.createElement('div');
    list.setAttribute('role', 'tablist');
    list.setAttribute('aria-label', 'Settings sections');
    list.className =
        'sticky top-0 z-20 -mx-1 mb-5 flex items-center gap-1 overflow-x-auto scrollbar-none ' +
        'border-b border-white/[0.06] bg-[#0a0a0c]/70 backdrop-blur-xl px-1';
    tabbarHost.replaceChildren(list);

    const buttons = new Map<string, HTMLButtonElement>();
    const badgeSlots = new Map<string, HTMLSpanElement>();
    const valid = tabs.filter((t) => sectionOf(t.id));

    for (const tab of valid) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.id = `settingstab-${tab.id}`;
        btn.setAttribute('role', 'tab');
        btn.setAttribute('aria-controls', `settings-sec-${tab.id}`);
        btn.setAttribute('data-testid', `settingstab-${tab.id}`);
        const labelSpan = document.createElement('span');
        labelSpan.textContent = tab.label;
        const badgeSlot = document.createElement('span');
        badgeSlot.setAttribute('data-testid', `settingstab-${tab.id}-badge`);
        btn.append(labelSpan, badgeSlot);
        badgeSlots.set(tab.id, badgeSlot);
        btn.addEventListener('click', () => void activate(tab.id, true));
        btn.addEventListener('keydown', onKeyNav);
        list.appendChild(btn);
        buttons.set(tab.id, btn);

        // Wire the (external) section as this tab's panel.
        const section = sectionOf(tab.id)!;
        section.setAttribute('role', 'tabpanel');
        section.setAttribute('aria-labelledby', `settingstab-${tab.id}`);
        section.setAttribute('tabindex', '0');
    }

    const ids = valid.map((t) => t.id);
    let activeId = ids[0]!;

    function paint(): void {
        for (const id of ids) {
            const btn = buttons.get(id)!;
            const section = sectionOf(id)!;
            const isActive = id === activeId;
            btn.className = tabBtnClass(isActive);
            btn.setAttribute('aria-selected', String(isActive));
            btn.tabIndex = isActive ? 0 : -1;
            section.classList.toggle('hidden', !isActive);
        }
    }

    function setHash(id: string): void {
        // Write `#/settings/<id>` via the shared router helper (replaceState —
        // no hashchange, no history spam; the top-level tab segment + ?params
        // are preserved).
        if (useHash) setHashView('settings', id);
    }

    /** Returns true if the tab actually switched (false when vetoed by guard). */
    async function activate(id: string, fromUser = false): Promise<boolean> {
        if (!buttons.has(id)) return false;
        if (id === activeId) {
            if (fromUser) buttons.get(id)?.focus();
            return true;
        }
        if (opts.guard) {
            const ok = await opts.guard(activeId, id);
            if (!ok) return false;
        }
        activeId = id;
        paint();
        if (fromUser) buttons.get(id)?.focus();
        setHash(id);
        opts.onChange?.(id);
        return true;
    }

    function onKeyNav(e: KeyboardEvent): void {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return;
        e.preventDefault();
        const cur = ids.indexOf(activeId);
        let next = cur;
        if (e.key === 'ArrowLeft') next = (cur - 1 + ids.length) % ids.length;
        else if (e.key === 'ArrowRight') next = (cur + 1) % ids.length;
        else if (e.key === 'Home') next = 0;
        else if (e.key === 'End') next = ids.length - 1;
        void activate(ids[next]!, true);
    }

    const onHashChange = (): void => {
        const id = hashSub();
        if (id && buttons.has(id) && id !== activeId) {
            void activate(id).then((switched) => {
                if (!switched) setHash(activeId); // vetoed → restore the hash
            });
        }
    };
    if (useHash) window.addEventListener('hashchange', onHashChange);

    // Initial selection: a valid sub in the hash wins, then opts.initial, then
    // the first tab. Canonicalize the hash so `#/settings` becomes
    // `#/settings/<start>` (shareable, and consistent with later switches).
    const fromHash = useHash ? hashSub() : '';
    const start =
        (fromHash && buttons.has(fromHash) && fromHash) ||
        (opts.initial && buttons.has(opts.initial) && opts.initial) ||
        ids[0]!;
    activeId = start;
    paint();
    setHash(start);

    function setBadge(id: string, badge: TabBadge | null): void {
        const slot = badgeSlots.get(id);
        if (!slot) return;
        slot.replaceChildren();
        if (!badge) return;
        const intent = badge.intent ?? 'info';
        const el = document.createElement('span');
        if (badge.text) {
            el.className =
                'ml-0.5 inline-flex items-center justify-center min-w-[1rem] px-1 h-4 rounded-full ' +
                `text-[9px] font-bold ${BADGE_TONE[intent]}`;
            el.textContent = badge.text;
        } else if (badge.dot) {
            el.className = `ml-0.5 inline-block h-1.5 w-1.5 rounded-full ${DOT_COLOR[intent]}`;
            el.setAttribute('aria-hidden', 'true');
        }
        slot.appendChild(el);
    }

    return {
        root: list,
        setActive: (id) => void activate(id),
        getActive: () => activeId,
        setBadge,
        destroy: () => {
            if (useHash) window.removeEventListener('hashchange', onHashChange);
        },
    };
}
