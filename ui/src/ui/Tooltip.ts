/**
 * Tooltip primitive — popover-style hint anchored to a trigger element.
 *
 * Replaces the bare `title=""` attribute approach for cases where we need
 * styling control, ARIA, or richer content. Triggers on hover (after a
 * delay) and on keyboard focus (immediate, for a11y).
 *
 * Renders the tooltip into a single shared host (`#llmproxy-tooltip-host`)
 * so we don't pollute the trigger element's stacking context. Auto-flips
 * vertically when the preferred placement would clip the viewport.
 */
import { cx } from './classnames';

export type TooltipPlacement = 'top' | 'bottom';

export interface AttachTooltipOptions {
    content: string;
    placement?: TooltipPlacement;
    /** Hover delay in ms before showing (focus shows immediately). Defaults to 200. */
    delay?: number;
    /** Visual variant; danger uses a rose tint for warnings. */
    intent?: 'neutral' | 'danger';
}

let _host: HTMLElement | null = null;
let _counter = 0;

function ensureHost(): HTMLElement {
    if (_host && document.body.contains(_host)) return _host;
    _host = document.createElement('div');
    _host.id = 'llmproxy-tooltip-host';
    _host.className = 'fixed inset-0 pointer-events-none z-[180]';
    _host.setAttribute('aria-hidden', 'true');
    document.body.appendChild(_host);
    return _host;
}

function position(target: HTMLElement, popover: HTMLElement, placement: TooltipPlacement): void {
    const rect = target.getBoundingClientRect();
    const pop = popover.getBoundingClientRect();
    const margin = 6;
    let top = placement === 'top' ? rect.top - pop.height - margin : rect.bottom + margin;
    // Auto-flip if the preferred placement would clip the viewport.
    if (placement === 'top' && top < 4) top = rect.bottom + margin;
    if (placement === 'bottom' && top + pop.height > window.innerHeight - 4) top = rect.top - pop.height - margin;

    let left = rect.left + rect.width / 2 - pop.width / 2;
    left = Math.max(4, Math.min(left, window.innerWidth - pop.width - 4));

    popover.style.top = `${Math.round(top)}px`;
    popover.style.left = `${Math.round(left)}px`;
}

const INTENT_CLASS: Record<NonNullable<AttachTooltipOptions['intent']>, string> = {
    neutral: 'bg-[#0a0a0c] text-slate-200 border-white/[0.1]',
    danger: 'bg-rose-500/15 text-rose-200 border-rose-500/30',
};

/**
 * Attach a tooltip to `target`. Returns a `destroy()` cleanup function.
 * Calling destroy detaches all listeners and removes any visible popover.
 */
export function attachTooltip(target: HTMLElement, opts: AttachTooltipOptions): () => void {
    const placement = opts.placement ?? 'top';
    const delay = opts.delay ?? 200;
    const intent = opts.intent ?? 'neutral';
    const id = `tip-${++_counter}`;

    let popover: HTMLElement | null = null;
    let showTimer: ReturnType<typeof setTimeout> | null = null;

    target.setAttribute('aria-describedby', id);

    const show = (): void => {
        if (popover) return;
        const host = ensureHost();
        popover = document.createElement('div');
        popover.id = id;
        popover.setAttribute('role', 'tooltip');
        popover.className = cx(
            'absolute pointer-events-none px-2 py-1 rounded-md border shadow-lg',
            'text-[11px] font-mono leading-snug max-w-xs whitespace-normal',
            'opacity-0 transition-opacity duration-100',
            INTENT_CLASS[intent]
        );
        popover.textContent = opts.content;
        host.appendChild(popover);
        // Force layout so the transition runs.
        position(target, popover, placement);
        requestAnimationFrame(() => popover?.classList.replace('opacity-0', 'opacity-100'));
    };

    const hide = (): void => {
        if (showTimer) {
            clearTimeout(showTimer);
            showTimer = null;
        }
        if (popover) {
            popover.remove();
            popover = null;
        }
    };

    const onEnter = (): void => {
        if (showTimer) return;
        showTimer = setTimeout(() => {
            showTimer = null;
            show();
        }, delay);
    };

    const onFocus = (): void => {
        if (showTimer) {
            clearTimeout(showTimer);
            showTimer = null;
        }
        show();
    };

    target.addEventListener('mouseenter', onEnter);
    target.addEventListener('mouseleave', hide);
    target.addEventListener('focus', onFocus);
    target.addEventListener('blur', hide);

    return () => {
        hide();
        target.removeEventListener('mouseenter', onEnter);
        target.removeEventListener('mouseleave', hide);
        target.removeEventListener('focus', onFocus);
        target.removeEventListener('blur', hide);
        target.removeAttribute('aria-describedby');
    };
}
