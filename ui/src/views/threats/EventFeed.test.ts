import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ThreatEventFeed, isSecurityEvent, muteKeyFor } from './EventFeed';
import type { SecurityEvent } from './types';

class FakeEventSource {
    public onopen: (() => void) | null = null;
    public onmessage: ((ev: MessageEvent) => void) | null = null;
    public onerror: (() => void) | null = null;
    public closed = false;
    constructor(public readonly url: string) {}
    close(): void {
        this.closed = true;
    }
    /** Test helper. */
    emit(data: unknown): void {
        this.onmessage?.(new MessageEvent('message', { data: JSON.stringify(data) }));
    }
    error(): void {
        this.onerror?.();
    }
    open(): void {
        this.onopen?.();
    }
}

function makeStorage(initial: Record<string, string> = {}): Storage {
    const store = new Map(Object.entries(initial));
    return {
        getItem: (k) => store.get(k) ?? null,
        setItem: (k, v) => {
            store.set(k, v);
        },
        removeItem: (k) => {
            store.delete(k);
        },
        clear: () => store.clear(),
        key: (i) => Array.from(store.keys())[i] ?? null,
        get length() {
            return store.size;
        },
    } as Storage;
}

describe('muteKeyFor()', () => {
    it('uses the explicit signature when present', () => {
        expect(muteKeyFor({ level: 'security', signature: 'rl_429' })).toBe('SECURITY:rl_429');
    });

    it('falls back to a truncated message hash', () => {
        const k = muteKeyFor({
            level: 'WARNING',
            message: 'Auth failed for user fab — bad credentials provided yet again',
        });
        expect(k.startsWith('WARNING:')).toBe(true);
        expect(k.length).toBeLessThanOrEqual('WARNING:'.length + 32);
    });

    it('upper-cases the level so case differences collapse', () => {
        expect(muteKeyFor({ level: 'security', signature: 's' })).toBe(
            muteKeyFor({ level: 'SECURITY', signature: 's' })
        );
    });
});

describe('isSecurityEvent()', () => {
    it('accepts security/warning/error/critical levels', () => {
        for (const level of ['SECURITY', 'warning', 'Error', 'CRITICAL']) {
            expect(isSecurityEvent({ level })).toBe(true);
        }
    });

    it('matches keywords in INFO messages', () => {
        expect(isSecurityEvent({ level: 'INFO', message: 'PII redaction applied' })).toBe(true);
        expect(isSecurityEvent({ level: 'INFO', message: 'request 404 to /v1/foo' })).toBe(false);
    });
});

describe('ThreatEventFeed', () => {
    let container: HTMLElement;
    let storage: Storage;
    let createdSources: FakeEventSource[];
    let factory: (url: string) => EventSource;

    beforeEach(() => {
        container = document.createElement('div');
        document.body.appendChild(container);
        storage = makeStorage();
        createdSources = [];
        factory = (url: string) => {
            const es = new FakeEventSource(url);
            createdSources.push(es);
            return es as unknown as EventSource;
        };
        vi.useFakeTimers();
    });

    afterEach(() => {
        document.body.removeChild(container);
        vi.useRealTimers();
    });

    it('shows an empty state with no events streamed', () => {
        new ThreatEventFeed(container, { storage, getToken: () => 'tk', eventSourceFactory: factory });
        const empty = container.querySelector('[data-testid="threat-feed-empty"]');
        expect(empty).not.toBeNull();
    });

    it('opens an EventSource against /api/v1/logs and streams events into the list', () => {
        const feed = new ThreatEventFeed(container, {
            storage,
            getToken: () => 'TOKEN',
            eventSourceFactory: factory,
            mintSseToken: () => 'SSE_TOKEN',
        });
        feed.connect();
        vi.runAllTimers();
        const es = createdSources[0]!;
        expect(es.url).toContain('/api/v1/logs?sse_token=SSE_TOKEN');
        es.open();
        es.emit({ level: 'SECURITY', message: 'WAF blocked', req_id: 'r1', signature: 'rl_429' });
        const list = container.querySelector('[data-testid="threat-feed-list"]') as HTMLElement;
        expect(list.textContent).toContain('WAF blocked');
        feed.disconnect();
        expect(es.closed).toBe(true);
    });

    it('adds Investigate + Explain actions when req_id and signature are present', () => {
        const feed = new ThreatEventFeed(container, {
            storage, getToken: () => 'TOKEN', eventSourceFactory: factory, mintSseToken: () => 'SSE_TOKEN',
        });
        feed.addEvent({ level: 'SECURITY', message: 'rl', req_id: 'abc', signature: 'rl_429' });
        const investigate = container.querySelector('[data-testid="threat-investigate-abc"]');
        const explain = container.querySelector('[data-testid="threat-explain-rl_429"]');
        expect(investigate).not.toBeNull();
        expect(explain).not.toBeNull();
        expect((investigate as HTMLElement)?.dataset.drilldown).toBe('request:abc');
        expect((explain as HTMLElement)?.dataset.explain).toBe('rule:rl_429');
    });

    it('mute persists to storage and hides the event', () => {
        const feed = new ThreatEventFeed(container, {
            storage, getToken: () => 'TOKEN', eventSourceFactory: factory, mintSseToken: () => 'SSE_TOKEN',
        });
        const ev: SecurityEvent = { level: 'WARNING', message: 'auth fail', signature: 'auth_bad' };
        feed.addEvent(ev);

        const muteBtn = container.querySelector(`[data-testid="threat-mute-${muteKeyFor(ev)}"]`) as HTMLButtonElement;
        expect(muteBtn).not.toBeNull();
        muteBtn.click();

        expect(feed.getMutedKeys()).toContain(muteKeyFor(ev));
        const stored = JSON.parse(storage.getItem('llmproxy:muted-threats') ?? '[]');
        expect(stored).toContain(muteKeyFor(ev));

        // Empty state shows because the only event is now muted
        expect(container.querySelector('[data-testid="threat-feed-empty"]')).not.toBeNull();
    });

    it('rehydrates the muted set from storage on construction', () => {
        const ev: SecurityEvent = { level: 'WARNING', signature: 'auth_bad' };
        storage.setItem('llmproxy:muted-threats', JSON.stringify([muteKeyFor(ev)]));

        const feed = new ThreatEventFeed(container, {
            storage, getToken: () => 'TOKEN', eventSourceFactory: factory, mintSseToken: () => 'SSE_TOKEN',
        });
        expect(feed.isMuted(ev)).toBe(true);
    });

    it('falls into the error state and shows a Reconnect button after repeated SSE errors', () => {
        const feed = new ThreatEventFeed(container, {
            storage, getToken: () => 'TOKEN', eventSourceFactory: factory, mintSseToken: () => 'SSE_TOKEN',
        });
        feed.connect();
        const es = createdSources[0]!;
        for (let i = 0; i < 6; i++) es.error();

        const errBlock = container.querySelector('[data-testid="threat-feed-error"]');
        expect(errBlock).not.toBeNull();
        const retry = errBlock?.querySelector('[data-testid="error-state-retry"]') as HTMLButtonElement;
        expect(retry).not.toBeNull();

        // Pressing retry opens a fresh EventSource
        retry.click();
        expect(createdSources.length).toBe(2);
    });

    it('schedules a soft retry while no auth token is present', () => {
        const tokens = ['', '', 'eventually'];
        let i = 0;
        const feed = new ThreatEventFeed(container, {
            storage,
            getToken: () => tokens[i++] ?? 'eventually',
            eventSourceFactory: factory,
            mintSseToken: () => 'SSE_TOKEN',
        });
        feed.connect();
        // First call had no token; nothing connects yet
        expect(createdSources).toHaveLength(0);
        vi.advanceTimersByTime(2_000);
        // Second call still no token; another retry scheduled
        expect(createdSources).toHaveLength(0);
        vi.advanceTimersByTime(2_000);
        // Third call sees a token, connects
        expect(createdSources).toHaveLength(1);
        feed.disconnect();
    });
});
