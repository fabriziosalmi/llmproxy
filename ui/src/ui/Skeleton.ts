import { cx } from './classnames';

export type SkeletonShape = 'line' | 'block' | 'circle';

export interface SkeletonOptions {
    shape?: SkeletonShape;
    /** CSS width — string like '100%' or '120px'. Defaults vary by shape. */
    width?: string;
    /** CSS height — string like '12px' or '40%'. Defaults vary by shape. */
    height?: string;
    /** Number of repeats stacked vertically. Useful for skeleton lists. */
    repeat?: number;
    /** Gap between repeats (Tailwind spacing token, e.g. 'gap-2'). */
    gap?: string;
    className?: string;
    /** Override the default 'Loading' aria-label. Set to '' to suppress. */
    ariaLabel?: string;
}

const BASE = 'block bg-white/[0.06] animate-[llm-skeleton-pulse_1.4s_ease-in-out_infinite]';
const SHAPE: Record<SkeletonShape, string> = {
    line: 'rounded-md',
    block: 'rounded-lg',
    circle: 'rounded-full aspect-square',
};

const DEFAULT_DIMS: Record<SkeletonShape, { width: string; height: string }> = {
    line: { width: '100%', height: '0.75rem' },
    block: { width: '100%', height: '6rem' },
    circle: { width: '2rem', height: '2rem' },
};

function makeOne(opts: SkeletonOptions): HTMLElement {
    const shape = opts.shape ?? 'line';
    const defaults = DEFAULT_DIMS[shape];
    const el = document.createElement('span');
    el.className = cx(BASE, SHAPE[shape], opts.className);
    el.style.width = opts.width ?? defaults.width;
    el.style.height = opts.height ?? defaults.height;
    return el;
}

export function createSkeleton(opts: SkeletonOptions = {}): HTMLElement {
    const repeat = Math.max(1, opts.repeat ?? 1);
    const ariaLabel = opts.ariaLabel === undefined ? 'Loading' : opts.ariaLabel;

    if (repeat === 1) {
        const el = makeOne(opts);
        if (ariaLabel) {
            el.setAttribute('role', 'status');
            el.setAttribute('aria-label', ariaLabel);
        } else {
            el.setAttribute('aria-hidden', 'true');
        }
        return el;
    }

    const wrap = document.createElement('div');
    wrap.className = cx('flex flex-col', opts.gap ?? 'gap-2');
    if (ariaLabel) {
        wrap.setAttribute('role', 'status');
        wrap.setAttribute('aria-label', ariaLabel);
    } else {
        wrap.setAttribute('aria-hidden', 'true');
    }

    for (let i = 0; i < repeat; i++) {
        const child = makeOne(opts);
        child.setAttribute('aria-hidden', 'true');
        wrap.appendChild(child);
    }
    return wrap;
}
