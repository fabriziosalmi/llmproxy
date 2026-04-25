import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { __resetForTests, rum, type RumEvent } from './rum';

describe('rum', () => {
    let events: RumEvent[];

    beforeEach(() => {
        events = [];
        rum.setSink({ track: (ev) => events.push(ev) });
    });

    afterEach(() => {
        __resetForTests();
    });

    it('hasSink() reflects setSink state', () => {
        expect(rum.hasSink()).toBe(true);
        rum.setSink(null);
        expect(rum.hasSink()).toBe(false);
    });

    it('pageView records the canonical type and timestamp', () => {
        const before = Date.now();
        rum.pageView('threats', { from: 'login' });
        const ev = events[0]!;
        expect(ev.type).toBe('page_view');
        expect(ev.name).toBe('threats');
        expect(ev.ts).toBeGreaterThanOrEqual(before);
        expect(ev.meta?.from).toBe('login');
    });

    it('successive pageView calls include previous_view_ms dwell', () => {
        vi.useFakeTimers();
        rum.pageView('threats');
        vi.advanceTimersByTime(2_500);
        rum.pageView('endpoints');
        vi.useRealTimers();
        expect(events).toHaveLength(2);
        expect(events[1]?.meta?.previous_view_ms).toBe(2_500);
    });

    it('tabChange threads `from` from the previous tabChange', () => {
        rum.tabChange('threats');
        rum.tabChange('endpoints');
        expect(events).toHaveLength(2);
        expect(events[0]?.meta?.from).toBeNull();
        expect(events[1]?.meta?.from).toBe('threats');
    });

    it('action and error pass through with their meta', () => {
        rum.action('add_endpoint', { provider: 'openai' });
        rum.error('fetch_failed', { route: '/v1/models' });
        expect(events.map((e) => e.type)).toEqual(['action', 'error']);
        expect(events[0]?.meta?.provider).toBe('openai');
        expect(events[1]?.meta?.route).toBe('/v1/models');
    });

    it('with no sink set, calls are no-ops (do not throw)', () => {
        rum.setSink(null);
        expect(() => rum.pageView('x')).not.toThrow();
        expect(events).toHaveLength(0);
    });

    it('flush() awaits the sink flush when defined', async () => {
        let flushed = false;
        rum.setSink({
            track: () => {},
            flush: async () => {
                await Promise.resolve();
                flushed = true;
            },
        });
        await rum.flush();
        expect(flushed).toBe(true);
    });
});
