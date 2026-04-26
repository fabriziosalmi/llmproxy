import { cx } from './classnames';
import { createSkeleton } from './Skeleton';
import { createSparkline, type SparklineColor } from './Sparkline';

export type MetricIntent = 'neutral' | 'primary' | 'success' | 'warning' | 'danger' | 'info';
export type MetricSize = 'sm' | 'md';

export interface MetricTileOptions {
    /** All-caps label shown above the value. */
    label: string;
    /** Pre-formatted value. Pass an empty string + loading=true to render the skeleton. */
    value: string;
    /** Optional secondary line below the value (e.g. "of $50.00 daily limit"). */
    sub?: string;
    intent?: MetricIntent;
    size?: MetricSize;
    /** Provenance text — surfaces "what is this metric, where does it come from, what window". */
    provenance?: string;
    /** Render a skeleton in place of the value. */
    loading?: boolean;
    /** Render an error placeholder instead of the value. */
    error?: string;
    /** Optional click target — usually wires to drilldown. */
    onClick?: (ev: MouseEvent) => void;
    /**
     * Optional inline sparkline below the value. Pass the data series
     * (typically 24 hourly buckets) and the surrounding tile takes care
     * of color matching the intent.
     */
    sparkline?: { data: number[]; color?: SparklineColor };
    className?: string;
    testId?: string;
}

const INTENT_SPARK: Record<MetricIntent, SparklineColor> = {
    neutral: 'slate',
    primary: 'rose',
    success: 'emerald',
    warning: 'amber',
    danger: 'rose',
    info: 'cyan',
};

const INTENT_TEXT: Record<MetricIntent, string> = {
    neutral: 'text-white',
    primary: 'text-rose-400',
    success: 'text-emerald-400',
    warning: 'text-amber-400',
    danger: 'text-red-400',
    info: 'text-sky-400',
};

const INTENT_BORDER: Record<MetricIntent, string> = {
    neutral: 'border-white/[0.06]',
    primary: 'border-rose-500/20',
    success: 'border-emerald-500/20',
    warning: 'border-amber-500/20',
    danger: 'border-red-500/20',
    info: 'border-sky-500/20',
};

const SIZE_VALUE: Record<MetricSize, string> = {
    sm: 'text-lg',
    md: 'text-2xl',
};

const INFO_ICON =
    '<svg viewBox="0 0 16 16" width="11" height="11" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">' +
    '<circle cx="8" cy="8" r="6.5"/><path d="M8 7.5v3.5"/><circle cx="8" cy="5.4" r="0.4" fill="currentColor"/></svg>';

export function createMetricTile(opts: MetricTileOptions): HTMLElement {
    const intent = opts.intent ?? 'neutral';
    const size = opts.size ?? 'md';

    const tile = document.createElement('article');
    tile.className = cx(
        'bg-white/[0.03] backdrop-blur-xl rounded-2xl border p-4',
        INTENT_BORDER[intent],
        opts.onClick &&
            'cursor-pointer transition-colors hover:bg-white/[0.05] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-500/40',
        opts.className
    );
    if (opts.testId) tile.setAttribute('data-testid', opts.testId);

    if (opts.onClick) {
        tile.tabIndex = 0;
        tile.setAttribute('role', 'button');
        tile.addEventListener('click', opts.onClick);
        tile.addEventListener('keydown', (ev) => {
            if (ev.key === 'Enter' || ev.key === ' ') {
                ev.preventDefault();
                opts.onClick?.(ev as unknown as MouseEvent);
            }
        });
    }

    // Header row: label + (optional) provenance ℹ
    const head = document.createElement('div');
    head.className = 'flex items-center justify-between mb-1';

    const labelEl = document.createElement('p');
    labelEl.className = cx(
        'text-[9px] font-bold uppercase tracking-widest',
        intent === 'neutral' ? 'text-slate-500' : INTENT_TEXT[intent]
    );
    labelEl.textContent = opts.label;
    head.appendChild(labelEl);

    if (opts.provenance) {
        const info = document.createElement('button');
        info.type = 'button';
        info.className =
            'shrink-0 text-slate-600 hover:text-slate-300 transition-colors p-0.5 -mr-0.5 rounded ' +
            'focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-rose-500/40';
        info.setAttribute('aria-label', `About ${opts.label}`);
        info.title = opts.provenance;
        info.innerHTML = INFO_ICON;
        // Stop propagation so clicking the info pip doesn't trigger the tile's onClick.
        info.addEventListener('click', (ev) => ev.stopPropagation());
        head.appendChild(info);
    }
    tile.appendChild(head);

    // Value
    const valueEl = document.createElement('p');
    valueEl.className = cx(SIZE_VALUE[size], 'font-black font-mono', INTENT_TEXT[intent]);
    if (opts.error) {
        valueEl.classList.add('text-red-400');
        valueEl.classList.remove(INTENT_TEXT[intent]);
        valueEl.textContent = '—';
        valueEl.title = opts.error;
        valueEl.setAttribute('aria-label', `${opts.label}: ${opts.error}`);
    } else if (opts.loading) {
        valueEl.replaceChildren(
            createSkeleton({ width: '70%', height: size === 'md' ? '1.75rem' : '1.25rem', ariaLabel: '' })
        );
        valueEl.setAttribute('aria-label', `${opts.label}: loading`);
    } else {
        valueEl.textContent = opts.value;
    }
    tile.appendChild(valueEl);

    if (opts.sub) {
        const subEl = document.createElement('p');
        subEl.className = 'text-[10px] font-mono text-slate-500 mt-0.5';
        subEl.textContent = opts.sub;
        tile.appendChild(subEl);
    }

    // Sparkline strip — sits below the value/sub so the eye lands on the
    // big number first. Skip when loading/error to avoid stamping a
    // confusing flat line over a skeleton.
    if (opts.sparkline && opts.sparkline.data.length >= 2 && !opts.loading && !opts.error) {
        const spark = createSparkline({
            data: opts.sparkline.data,
            color: opts.sparkline.color ?? INTENT_SPARK[intent],
            height: 28,
            ariaLabel: `${opts.label} trend`,
        });
        spark.classList.add('mt-2');
        tile.appendChild(spark);
    }

    return tile;
}
