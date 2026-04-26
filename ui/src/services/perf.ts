/**
 * Page-load perf metrics — FCP / LCP / DOMContentLoaded — routed through
 * the rum facade so they land in whatever sink is configured (default
 * no-op). Cheap, no third-party dep, tap-out-able.
 *
 * The Performance API exposes paint timings as historical entries: even
 * if we read them after FCP already fired, `performance.getEntriesByType`
 * still returns them. So this can boot lazily — we don't need the
 * observer to be installed before first paint.
 */

import { rum } from './rum';

interface PerfMetric {
    name: 'fcp' | 'lcp' | 'dcl' | 'load';
    /** Time relative to navigationStart, in milliseconds. */
    value_ms: number;
}

function _now(): number {
    return typeof performance !== 'undefined' ? performance.now() : 0;
}

function _readPaintEntries(): PerfMetric[] {
    if (typeof performance === 'undefined' || typeof performance.getEntriesByType !== 'function') {
        return [];
    }
    const out: PerfMetric[] = [];
    try {
        const paints = performance.getEntriesByType('paint');
        for (const e of paints) {
            if (e.name === 'first-contentful-paint') {
                out.push({ name: 'fcp', value_ms: Math.round(e.startTime) });
            }
        }
    } catch {
        /* paint timing not supported (older Safari) — skip */
    }
    return out;
}

function _readNavTiming(): PerfMetric[] {
    if (typeof performance === 'undefined' || typeof performance.getEntriesByType !== 'function') {
        return [];
    }
    const out: PerfMetric[] = [];
    try {
        const nav = performance.getEntriesByType('navigation')[0] as PerformanceNavigationTiming | undefined;
        if (nav) {
            if (nav.domContentLoadedEventEnd > 0) {
                out.push({ name: 'dcl', value_ms: Math.round(nav.domContentLoadedEventEnd) });
            }
            if (nav.loadEventEnd > 0) {
                out.push({ name: 'load', value_ms: Math.round(nav.loadEventEnd) });
            }
        }
    } catch {
        /* skip */
    }
    return out;
}

/**
 * Capture FCP + DCL right now, observe LCP until the user interacts.
 *
 * LCP fires multiple times as larger elements paint; the LAST entry
 * before user input is the one to report. We hook a one-shot listener
 * on first interaction (click / keydown / scroll) to snapshot the
 * largest LCP seen so far.
 */
export function reportPagePerf(): void {
    // Snapshot the metrics we already have.
    const initial = [..._readPaintEntries(), ..._readNavTiming()];
    for (const m of initial) {
        rum.action('perf_metric', { name: m.name, value_ms: m.value_ms });
    }

    // LCP — observer keeps the largest seen so far. On first user gesture
    // (or 30 s timeout) we report it and disconnect.
    if (typeof PerformanceObserver === 'undefined') return;

    let largestLcp = 0;
    let reported = false;
    let observer: PerformanceObserver | null = null;
    try {
        observer = new PerformanceObserver((list) => {
            for (const entry of list.getEntries()) {
                // LCP entry has `startTime` as the candidate paint time.
                if (entry.startTime > largestLcp) largestLcp = entry.startTime;
            }
        });
        observer.observe({ type: 'largest-contentful-paint', buffered: true });
    } catch {
        return; // browser doesn't support the LCP type
    }

    const reportLcp = (): void => {
        if (reported) return;
        reported = true;
        try {
            observer?.disconnect();
        } catch {
            /* already disconnected */
        }
        if (largestLcp > 0) {
            rum.action('perf_metric', { name: 'lcp', value_ms: Math.round(largestLcp) });
        }
    };

    if (typeof window !== 'undefined') {
        const onceOpts = { once: true, capture: true } as const;
        window.addEventListener('click', reportLcp, onceOpts);
        window.addEventListener('keydown', reportLcp, onceOpts);
        window.addEventListener('scroll', reportLcp, onceOpts);
        window.addEventListener('pagehide', reportLcp, onceOpts);
    }
    // Cap the wait so LCP always reports eventually.
    setTimeout(reportLcp, 30_000);
}

// ── Exported for tests ──────────────────────────────────────────────
export const __testInternals = { _readPaintEntries, _readNavTiming, _now };
