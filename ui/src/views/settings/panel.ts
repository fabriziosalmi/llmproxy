/**
 * settingsPanel — the one canonical card for Settings sections.
 *
 * Settings had two card styles side by side: the generic `createCard` primitive
 * (rounded-xl, p-4) used by Identity/RBAC/Webhooks/System/Export, and a
 * hand-rolled `rounded-2xl … p-6` string used by Guided config / Rate limit /
 * Routing / etc. Different radius + padding read as visual drift when the panels
 * stack. This helper is the single source of the Settings card look so every
 * section matches. Heading (and an optional right-aligned action) live in a
 * standard row so titles align across panels too.
 */
export interface SettingsPanelOptions {
    title?: string;
    /** Optional element rendered at the right of the heading row (e.g. an action button). */
    titleRight?: HTMLElement;
    body: HTMLElement | HTMLElement[];
    testId?: string;
}

export function settingsPanel(opts: SettingsPanelOptions): HTMLElement {
    const card = document.createElement('section');
    card.className = 'bg-white/[0.03] backdrop-blur-xl rounded-2xl border border-white/[0.06] p-6';
    if (opts.testId) card.setAttribute('data-testid', opts.testId);

    if (opts.title) {
        const head = document.createElement('div');
        head.className = 'flex items-center justify-between gap-3 mb-4';
        const h = document.createElement('h2');
        h.className = 'text-xs font-bold text-white';
        h.textContent = opts.title;
        head.appendChild(h);
        if (opts.titleRight) head.appendChild(opts.titleRight);
        card.appendChild(head);
    }

    for (const node of Array.isArray(opts.body) ? opts.body : [opts.body]) card.appendChild(node);
    return card;
}
