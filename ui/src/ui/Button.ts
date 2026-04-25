import { cx } from './classnames';

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'destructive';
export type ButtonSize = 'sm' | 'md' | 'lg';

export interface ButtonOptions {
    label: string;
    variant?: ButtonVariant;
    size?: ButtonSize;
    onClick?: (ev: MouseEvent) => void;
    /** Leading icon as raw SVG markup. Sanitize before passing untrusted input. */
    icon?: string;
    disabled?: boolean;
    /** Used when the visible label is an icon-only or otherwise non-descriptive symbol. */
    ariaLabel?: string;
    /** Pressed-toggle button (renders aria-pressed). */
    pressed?: boolean;
    type?: 'button' | 'submit' | 'reset';
    /** Additional classes appended after the variant/size classes. */
    className?: string;
    /** Forwarded to data-testid for e2e selectors. */
    testId?: string;
}

const VARIANT_CLASSES: Record<ButtonVariant, string> = {
    primary:
        'bg-rose-500/20 text-rose-300 border border-rose-500/30 hover:bg-rose-500/30 hover:text-rose-200 active:bg-rose-500/40',
    secondary:
        'bg-white/5 text-slate-200 border border-white/10 hover:bg-white/10 hover:border-white/20 active:bg-white/15',
    ghost: 'bg-transparent text-slate-300 hover:bg-white/5 hover:text-white border border-transparent',
    destructive:
        'bg-red-500/15 text-red-300 border border-red-500/25 hover:bg-red-500/25 hover:text-red-200 active:bg-red-500/35',
};

const SIZE_CLASSES: Record<ButtonSize, string> = {
    sm: 'h-7 px-2.5 text-[11px] gap-1.5',
    md: 'h-9 px-3.5 text-xs gap-2',
    lg: 'h-11 px-5 text-sm gap-2.5',
};

const BASE =
    'inline-flex items-center justify-center font-semibold rounded-lg transition-colors duration-150 ' +
    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-500/60 focus-visible:ring-offset-1 focus-visible:ring-offset-[#050608] ' +
    'disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:bg-inherit';

export function createButton(opts: ButtonOptions): HTMLButtonElement {
    const variant = opts.variant ?? 'secondary';
    const size = opts.size ?? 'md';

    const btn = document.createElement('button');
    btn.type = opts.type ?? 'button';
    btn.className = cx(BASE, VARIANT_CLASSES[variant], SIZE_CLASSES[size], opts.className);

    if (opts.icon) {
        const iconSpan = document.createElement('span');
        iconSpan.className = 'shrink-0 inline-flex items-center';
        iconSpan.innerHTML = opts.icon;
        iconSpan.setAttribute('aria-hidden', 'true');
        btn.appendChild(iconSpan);
    }

    const labelSpan = document.createElement('span');
    labelSpan.textContent = opts.label;
    btn.appendChild(labelSpan);

    if (opts.disabled) btn.disabled = true;
    if (opts.ariaLabel) btn.setAttribute('aria-label', opts.ariaLabel);
    if (typeof opts.pressed === 'boolean') btn.setAttribute('aria-pressed', String(opts.pressed));
    if (opts.testId) btn.setAttribute('data-testid', opts.testId);
    if (opts.onClick) btn.addEventListener('click', opts.onClick);

    return btn;
}
