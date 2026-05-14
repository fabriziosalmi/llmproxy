/**
 * Sparkline — minimal inline SVG line chart with an optional area fill.
 *
 * Pure SVG, no runtime dep. Used inside MetricTile (and free-standing
 * anywhere else) to surface a 24-point trend below a single big number.
 *
 * Design choices:
 *  - Aspect-ratio is wide (12:1 default) so it reads as a "trend strip",
 *    not a chart you'd inspect — that role belongs to the drilldown.
 *  - One stroke + one filled area path; the area uses a vertical gradient
 *    fading to transparent so the value bands above stay legible.
 *  - When the series is constant (e.g. all zeros at boot), the line sits
 *    at the vertical mid-line instead of dividing by zero. Small visual
 *    win: empty surfaces look intentional, not broken.
 */

import { cx } from './classnames';

export type SparklineColor = 'cyan' | 'emerald' | 'amber' | 'rose' | 'slate';

export interface SparklineOptions {
    /** Y values, oldest → newest. Anything ≥ 2 entries renders. */
    data: number[];
    /**
     * Stroke color. Maps to a Tailwind palette token; a gradient fill is
     * derived from the same hue. Defaults to 'cyan'.
     */
    color?: SparklineColor;
    /** Render with an area fill under the line. Defaults to true. */
    area?: boolean;
    /** Pixel height. Width follows via viewBox aspect ratio. Defaults to 28. */
    height?: number;
    /** Aspect ratio width:height. Defaults to 12. */
    aspect?: number;
    className?: string;
    testId?: string;
    /** Accessible label — defaults to a generic "Trend" string. */
    ariaLabel?: string;
}

const STROKE_HEX: Record<SparklineColor, string> = {
    cyan: '#22d3ee',
    emerald: '#34d399',
    amber: '#fbbf24',
    rose: '#fb7185',
    slate: '#94a3b8',
};

let _gradientCounter = 0;

export function createSparkline(opts: SparklineOptions): SVGSVGElement {
    const data = opts.data ?? [];
    const color = opts.color ?? 'cyan';
    const area = opts.area !== false;
    const h = opts.height ?? 28;
    const aspect = opts.aspect ?? 12;
    const w = Math.round(h * aspect);
    const stroke = STROKE_HEX[color];

    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
    svg.setAttribute('preserveAspectRatio', 'none');
    svg.setAttribute('width', '100%');
    svg.setAttribute('height', String(h));
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', opts.ariaLabel ?? 'Trend');
    svg.classList.add('block');
    if (opts.className) {
        for (const cls of cx(opts.className).split(/\s+/).filter(Boolean)) svg.classList.add(cls);
    }
    if (opts.testId) svg.setAttribute('data-testid', opts.testId);

    if (data.length < 2) return svg;

    // Map data → SVG points. Pad 1px top/bottom so the stroke isn't clipped.
    const pad = 1;
    const min = Math.min(...data);
    const max = Math.max(...data);
    const isFlat = max === min;
    const span = isFlat ? 1 : max - min;

    const points: string[] = [];
    const step = w / (data.length - 1);
    for (let i = 0; i < data.length; i++) {
        const x = i * step;
        const v = data[i] ?? 0;
        // Flat series: pin to the mid-line so the strip doesn't slam top
        // (which would happen with (v-min)/1 when min === v === max).
        const norm = isFlat ? 0.5 : (v - min) / span;
        const y = pad + (1 - norm) * (h - 2 * pad);
        points.push(`${x.toFixed(2)},${y.toFixed(2)}`);
    }

    // Area fill — close the path back to the bottom-right and bottom-left.
    if (area) {
        const gradId = `spark-grad-${++_gradientCounter}`;
        const defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');
        const grad = document.createElementNS('http://www.w3.org/2000/svg', 'linearGradient');
        grad.setAttribute('id', gradId);
        grad.setAttribute('x1', '0%');
        grad.setAttribute('y1', '0%');
        grad.setAttribute('x2', '0%');
        grad.setAttribute('y2', '100%');
        const stopTop = document.createElementNS('http://www.w3.org/2000/svg', 'stop');
        stopTop.setAttribute('offset', '0%');
        stopTop.setAttribute('stop-color', stroke);
        stopTop.setAttribute('stop-opacity', '0.35');
        const stopBot = document.createElementNS('http://www.w3.org/2000/svg', 'stop');
        stopBot.setAttribute('offset', '100%');
        stopBot.setAttribute('stop-color', stroke);
        stopBot.setAttribute('stop-opacity', '0');
        grad.appendChild(stopTop);
        grad.appendChild(stopBot);
        defs.appendChild(grad);
        svg.appendChild(defs);

        const areaPath = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        areaPath.setAttribute('d', `M${points[0]} L${points.join(' L')} L${w},${h} L0,${h} Z`);
        areaPath.setAttribute('fill', `url(#${gradId})`);
        svg.appendChild(areaPath);
    }

    const line = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
    line.setAttribute('points', points.join(' '));
    line.setAttribute('fill', 'none');
    line.setAttribute('stroke', stroke);
    line.setAttribute('stroke-width', '1.5');
    line.setAttribute('stroke-linejoin', 'round');
    line.setAttribute('stroke-linecap', 'round');
    svg.appendChild(line);

    return svg;
}
