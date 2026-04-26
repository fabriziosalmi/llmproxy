import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { reportPagePerf } from './perf';
import { rum, __resetForTests } from './rum';

describe('perf service', () => {
    let trackedEvents: Array<{ type: string; name: string; meta?: Record<string, unknown> }>;

    beforeEach(() => {
        trackedEvents = [];
        __resetForTests();
        rum.setSink({
            track: (ev) => {
                trackedEvents.push({ type: ev.type, name: ev.name, meta: ev.meta });
            },
        });
    });

    afterEach(() => {
        __resetForTests();
        vi.restoreAllMocks();
    });

    it('routes the FCP entry through rum.action with rounded ms', () => {
        const getEntriesByType = vi.spyOn(performance, 'getEntriesByType').mockImplementation((kind: string) => {
            if (kind === 'paint') {
                return [
                    // first-paint should be ignored — we only report FCP.
                    { name: 'first-paint', startTime: 100.4 } as PerformanceEntry,
                    { name: 'first-contentful-paint', startTime: 234.7 } as PerformanceEntry,
                ];
            }
            if (kind === 'navigation') {
                return [];
            }
            return [];
        });

        reportPagePerf();

        const fcp = trackedEvents.find((e) => e.name === 'perf_metric' && e.meta?.name === 'fcp');
        expect(fcp).toBeDefined();
        expect(fcp?.meta?.value_ms).toBe(235);
        // first-paint must NOT have been reported.
        const fp = trackedEvents.find((e) => e.meta?.name === 'first-paint');
        expect(fp).toBeUndefined();

        getEntriesByType.mockRestore();
    });

    it('reports DCL + load when the navigation timing entry is present', () => {
        const navEntry = { domContentLoadedEventEnd: 412.3, loadEventEnd: 510.9 } as PerformanceNavigationTiming;
        const getEntriesByType = vi.spyOn(performance, 'getEntriesByType').mockImplementation((kind: string) => {
            if (kind === 'navigation') return [navEntry as PerformanceEntry];
            return [];
        });

        reportPagePerf();

        const dcl = trackedEvents.find((e) => e.meta?.name === 'dcl');
        const load = trackedEvents.find((e) => e.meta?.name === 'load');
        expect(dcl?.meta?.value_ms).toBe(412);
        expect(load?.meta?.value_ms).toBe(511);

        getEntriesByType.mockRestore();
    });

    it('does not throw when getEntriesByType is missing entirely', () => {
        const original = performance.getEntriesByType;
        // Replace with a function that throws — covers older Safari quirks.
        (performance as unknown as { getEntriesByType: () => never }).getEntriesByType = () => {
            throw new Error('not supported');
        };
        try {
            expect(() => reportPagePerf()).not.toThrow();
        } finally {
            (performance as unknown as { getEntriesByType: typeof original }).getEntriesByType = original;
        }
    });

    it('skips DCL/load when timings are zero (event not yet fired)', () => {
        const navEntry = { domContentLoadedEventEnd: 0, loadEventEnd: 0 } as PerformanceNavigationTiming;
        const getEntriesByType = vi.spyOn(performance, 'getEntriesByType').mockImplementation((kind: string) => {
            if (kind === 'navigation') return [navEntry as PerformanceEntry];
            return [];
        });

        reportPagePerf();

        expect(trackedEvents.find((e) => e.meta?.name === 'dcl')).toBeUndefined();
        expect(trackedEvents.find((e) => e.meta?.name === 'load')).toBeUndefined();

        getEntriesByType.mockRestore();
    });
});
