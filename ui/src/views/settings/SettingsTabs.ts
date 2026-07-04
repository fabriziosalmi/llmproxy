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
 */

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
}

export interface SettingsTabsHandle {
    root: HTMLElement;
    setActive(id: string): void;
    getActive(): string;
    /** Detach listeners (hashchange). */
    destroy(): void;
}

const HASH_PREFIX = '#settings/';

function tabBtnClass(active: boolean): string {
    return [
        'inline-flex shrink-0 items-center gap-2 px-3.5 py-2 text-[11px] font-semibold border-b-2 -mb-px',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500/40 rounded-t transition-colors',
        active
            ? 'text-white border-cyan-500/60'
            : 'text-slate-500 border-transparent hover:text-slate-200 hover:border-white/10',
    ].join(' ');
}

/** Parse `#settings/<id>` → id, or null. */
export function tabFromHash(hash: string): string | null {
    if (!hash.startsWith(HASH_PREFIX)) return null;
    const id = hash.slice(HASH_PREFIX.length).replace(/[^a-z0-9_-]/gi, '');
    return id || null;
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
    const valid = tabs.filter((t) => sectionOf(t.id));

    for (const tab of valid) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.id = `settingstab-${tab.id}`;
        btn.setAttribute('role', 'tab');
        btn.setAttribute('aria-controls', `settings-sec-${tab.id}`);
        btn.setAttribute('data-testid', `settingstab-${tab.id}`);
        btn.textContent = tab.label;
        btn.addEventListener('click', () => activate(tab.id, true));
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

    let _suppressHash = false;
    function activate(id: string, fromUser = false): void {
        if (!buttons.has(id)) return;
        const changed = id !== activeId;
        activeId = id;
        paint();
        if (fromUser) buttons.get(id)?.focus();
        if (useHash) {
            _suppressHash = true;
            try {
                location.hash = HASH_PREFIX + id;
            } finally {
                _suppressHash = false;
            }
        }
        if (changed) opts.onChange?.(id);
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
        activate(ids[next]!, true);
    }

    const onHashChange = (): void => {
        if (_suppressHash) return;
        const id = tabFromHash(location.hash);
        if (id && buttons.has(id)) activate(id);
    };
    if (useHash) window.addEventListener('hashchange', onHashChange);

    // Initial selection: valid hash wins, then opts.initial, then first tab.
    const fromHash = useHash ? tabFromHash(location.hash) : null;
    const start = (fromHash && buttons.has(fromHash) && fromHash) || (opts.initial && buttons.has(opts.initial) && opts.initial) || ids[0]!;
    activeId = start;
    paint();

    return {
        root: list,
        setActive: (id) => activate(id),
        getActive: () => activeId,
        destroy: () => {
            if (useHash) window.removeEventListener('hashchange', onHashChange);
        },
    };
}
