import { cx } from './classnames';

export type BadgeIntent = 'neutral' | 'primary' | 'success' | 'warning' | 'danger' | 'info';
export type BadgeSize = 'sm' | 'md';

export interface BadgeOptions {
    label: string;
    intent?: BadgeIntent;
    size?: BadgeSize;
    /** Optional leading dot — useful for status indicators. */
    dot?: boolean;
    /**
     * Animate the dot with a slow breathing pulse (2.4s) — surfaces "live"
     * for success-intent indicators (Endpoint Live, sidebar proxy active).
     * Honors `prefers-reduced-motion` via the .pulse-live CSS rule.
     * Has no effect when `dot` is false.
     */
    pulse?: boolean;
    /** Optional leading icon as raw SVG. */
    icon?: string;
    /** Override the rendered title attribute (defaults to label for ellipsized cases). */
    title?: string;
    className?: string;
    testId?: string;
}

const INTENT_CLASSES: Record<BadgeIntent, { bg: string; text: string; border: string; dot: string }> = {
    neutral: { bg: 'bg-white/5', text: 'text-slate-300', border: 'border-white/10', dot: 'bg-slate-400' },
    primary: { bg: 'bg-rose-500/15', text: 'text-rose-300', border: 'border-rose-500/25', dot: 'bg-rose-400' },
    success: {
        bg: 'bg-emerald-500/15',
        text: 'text-emerald-300',
        border: 'border-emerald-500/25',
        dot: 'bg-emerald-400',
    },
    warning: { bg: 'bg-amber-500/15', text: 'text-amber-300', border: 'border-amber-500/25', dot: 'bg-amber-400' },
    danger: { bg: 'bg-red-500/15', text: 'text-red-300', border: 'border-red-500/25', dot: 'bg-red-400' },
    info: { bg: 'bg-blue-500/15', text: 'text-blue-300', border: 'border-blue-500/25', dot: 'bg-blue-400' },
};

const SIZE_CLASSES: Record<BadgeSize, string> = {
    sm: 'h-5 px-1.5 text-[10px] gap-1',
    md: 'h-6 px-2 text-[11px] gap-1.5',
};

const BASE = 'inline-flex items-center font-mono font-medium rounded-md border whitespace-nowrap';

export function createBadge(opts: BadgeOptions): HTMLElement {
    const intent = opts.intent ?? 'neutral';
    const size = opts.size ?? 'sm';
    const palette = INTENT_CLASSES[intent];

    const badge = document.createElement('span');
    badge.className = cx(BASE, palette.bg, palette.text, palette.border, SIZE_CLASSES[size], opts.className);
    badge.title = opts.title ?? opts.label;
    if (opts.testId) badge.setAttribute('data-testid', opts.testId);

    if (opts.dot) {
        const dot = document.createElement('span');
        dot.className = cx('inline-block w-1.5 h-1.5 rounded-full', palette.dot, opts.pulse && 'pulse-live');
        dot.setAttribute('aria-hidden', 'true');
        badge.appendChild(dot);
    }

    if (opts.icon) {
        const iconSpan = document.createElement('span');
        iconSpan.className = 'shrink-0 inline-flex items-center';
        iconSpan.innerHTML = opts.icon;
        iconSpan.setAttribute('aria-hidden', 'true');
        badge.appendChild(iconSpan);
    }

    const labelSpan = document.createElement('span');
    labelSpan.textContent = opts.label;
    badge.appendChild(labelSpan);

    return badge;
}
