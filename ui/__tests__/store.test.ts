import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { store } from '../services/store.js';

const initialState = JSON.parse(JSON.stringify(store.state));

describe('store', () => {
    beforeEach(() => {
        store.state = JSON.parse(JSON.stringify(initialState));
        store.listeners.length = 0;
    });

    it('starts on the threats tab with proxy enabled', () => {
        expect(store.state.currentTab).toBe('threats');
        expect(store.state.proxyEnabled).toBe(true);
        expect(store.state.firewall.enabled).toBe(true);
    });

    it('update() merges patches and preserves untouched keys', () => {
        store.update({ currentTab: 'guards', priorityMode: true });

        expect(store.state.currentTab).toBe('guards');
        expect(store.state.priorityMode).toBe(true);
        expect(store.state.proxyEnabled).toBe(true);
    });

    it('notify() fans out to every subscriber with the current state', () => {
        const a = vi.fn();
        const b = vi.fn();
        store.subscribe(a);
        store.subscribe(b);

        store.update({ currentTab: 'analytics' });

        expect(a).toHaveBeenCalledTimes(1);
        expect(b).toHaveBeenCalledTimes(1);
        expect(a.mock.calls[0]?.[0]?.currentTab).toBe('analytics');
        expect(b.mock.calls[0]?.[0]?.currentTab).toBe('analytics');
    });

    it('subscriber errors do not silently swallow callbacks before them', () => {
        const calls: string[] = [];
        store.subscribe(() => calls.push('a'));
        store.subscribe(() => {
            throw new Error('boom');
        });
        store.subscribe(() => calls.push('c'));

        // Documents current behaviour: a single throwing subscriber currently
        // takes down the whole notify pipeline. If we change `notify` to wrap
        // each call in try/catch this assertion will need to be flipped.
        expect(() => store.update({ currentTab: 'logs' })).toThrow('boom');
        expect(calls).toEqual(['a']);
    });
});

describe('store.poll', () => {
    let originalHidden: PropertyDescriptor | undefined;

    beforeEach(() => {
        vi.useFakeTimers();
        store.state = JSON.parse(JSON.stringify(initialState));
        store.listeners.length = 0;
        originalHidden = Object.getOwnPropertyDescriptor(Document.prototype, 'hidden');
        Object.defineProperty(document, 'hidden', { configurable: true, get: () => false });
    });

    afterEach(() => {
        vi.useRealTimers();
        if (originalHidden) {
            Object.defineProperty(Document.prototype, 'hidden', originalHidden);
        }
    });

    it('skips ticks while the document is hidden', () => {
        const fn = vi.fn();
        Object.defineProperty(document, 'hidden', { configurable: true, get: () => true });

        const stop = store.poll(fn, 100);
        vi.advanceTimersByTime(500);

        expect(fn).not.toHaveBeenCalled();
        stop();
    });

    it('only invokes the callback when the active tab matches requiredTab', () => {
        const fn = vi.fn();
        store.update({ currentTab: 'analytics' });

        const stop = store.poll(fn, 50, 'threats');
        vi.advanceTimersByTime(200);
        expect(fn).not.toHaveBeenCalled();

        store.update({ currentTab: 'threats' });
        vi.advanceTimersByTime(200);
        expect(fn).toHaveBeenCalled();
        stop();
    });

    it('stop() cancels the interval', () => {
        const fn = vi.fn();
        const stop = store.poll(fn, 25);

        vi.advanceTimersByTime(100);
        const callsBeforeStop = fn.mock.calls.length;
        expect(callsBeforeStop).toBeGreaterThan(0);

        stop();
        vi.advanceTimersByTime(500);
        expect(fn.mock.calls.length).toBe(callsBeforeStop);
    });
});
