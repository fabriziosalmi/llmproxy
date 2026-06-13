import { cx } from './classnames';

export type CardElevation = 'flat' | 'raised';

export interface CardOptions {
    /** Optional rendered header (use createCardHeader). */
    header?: HTMLElement;
    /** Body content as a node, list of nodes, or HTML string (sanitize untrusted input). */
    body?: HTMLElement | HTMLElement[] | string;
    /** Optional footer slot — usually action buttons. */
    footer?: HTMLElement;
    elevation?: CardElevation;
    /** Adds an interactive hover/focus treatment + tabindex=0. Pair with onClick. */
    interactive?: boolean;
    onClick?: (ev: MouseEvent) => void;
    className?: string;
    testId?: string;
}

const BASE = 'rounded-xl border bg-white/[0.03] backdrop-blur-xl';
const ELEVATION: Record<CardElevation, string> = {
    flat: 'border-white/[0.06]',
    raised: 'border-white/[0.08] shadow-[0_4px_12px_rgba(0,0,0,0.3)]',
};
const INTERACTIVE =
    'cursor-pointer transition-colors hover:bg-white/[0.05] hover:border-white/[0.12] ' +
    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-500/40';

export function createCard(opts: CardOptions = {}): HTMLElement {
    const card = document.createElement('article');
    card.className = cx(BASE, ELEVATION[opts.elevation ?? 'flat'], opts.interactive && INTERACTIVE, opts.className);

    if (opts.interactive) {
        card.tabIndex = 0;
        card.setAttribute('role', 'button');
    }
    if (opts.onClick) {
        card.addEventListener('click', opts.onClick);
        if (opts.interactive) {
            card.addEventListener('keydown', (ev) => {
                if (ev.key === 'Enter' || ev.key === ' ') {
                    ev.preventDefault();
                    opts.onClick?.(ev as unknown as MouseEvent);
                }
            });
        }
    }
    if (opts.testId) card.setAttribute('data-testid', opts.testId);

    if (opts.header) card.appendChild(opts.header);

    if (opts.body !== undefined) {
        const bodyEl = document.createElement('div');
        bodyEl.className = 'p-4';
        if (typeof opts.body === 'string') {
            bodyEl.textContent = opts.body;
        } else if (Array.isArray(opts.body)) {
            for (const child of opts.body) bodyEl.appendChild(child);
        } else {
            bodyEl.appendChild(opts.body);
        }
        card.appendChild(bodyEl);
    }

    if (opts.footer) card.appendChild(opts.footer);

    return card;
}

export function createCardHeader(title: string, subtitle?: string): HTMLElement {
    const header = document.createElement('header');
    header.className = 'px-4 pt-4 pb-2 border-b border-white/[0.04] flex flex-col gap-0.5';

    const h = document.createElement('h3');
    h.className = 'text-sm font-semibold text-white tracking-tight';
    h.textContent = title;
    header.appendChild(h);

    if (subtitle) {
        const sub = document.createElement('p');
        sub.className = 'text-[11px] text-slate-400 font-mono';
        sub.textContent = subtitle;
        header.appendChild(sub);
    }
    return header;
}
