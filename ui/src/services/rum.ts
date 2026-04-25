/**
 * Frontend RUM (real-user monitoring) facade.
 *
 * Default sink is no-op so the proxy doesn't ship a dependency on a
 * specific vendor. Operators who care about page-view or session
 * analytics plug in their own sink at boot:
 *
 *   import { rum } from './services/rum';
 *   import posthog from 'posthog-js';
 *   posthog.init('phc_…', { api_host: 'https://posthog.example.com' });
 *   rum.setSink({ track: (ev) => posthog.capture(ev.type, ev) });
 *
 * The facade tracks four canonical event types — page views, tab changes,
 * named actions, and errors. It never bundles a vendor SDK.
 */

export type RumEventType = 'page_view' | 'tab_change' | 'action' | 'error';

export interface RumEvent {
    type: RumEventType;
    /** Identifier for the surface — e.g. 'threats', 'add_endpoint'. */
    name: string;
    /** Optional metadata. Caller is responsible for not leaking PII. */
    meta?: Record<string, unknown>;
    /** Epoch ms; assigned by track() if absent. */
    ts?: number;
}

export interface RumSink {
    track(event: RumEvent): void;
    /** Optional: called by `rum.flush()` for backends with their own queue. */
    flush?(): Promise<void> | void;
}

let _sink: RumSink | null = null;
let _lastTab: string | null = null;
let _pageViewedAt: number | null = null;

export const rum = {
    /** Plug in a vendor sink. Pass null to detach (returns to no-op). */
    setSink(sink: RumSink | null): void {
        _sink = sink;
    },

    /** Has a sink been registered? Useful in tests. */
    hasSink(): boolean {
        return _sink !== null;
    },

    pageView(name: string, meta?: Record<string, unknown>): void {
        const now = Date.now();
        const dwell = _pageViewedAt ? now - _pageViewedAt : null;
        _pageViewedAt = now;
        _sink?.track({
            type: 'page_view',
            name,
            ts: now,
            meta: { ...meta, ...(dwell !== null ? { previous_view_ms: dwell } : {}) },
        });
    },

    tabChange(to: string, meta?: Record<string, unknown>): void {
        const from = _lastTab;
        _lastTab = to;
        _sink?.track({ type: 'tab_change', name: to, ts: Date.now(), meta: { ...meta, from } });
    },

    action(name: string, meta?: Record<string, unknown>): void {
        _sink?.track({ type: 'action', name, ts: Date.now(), meta });
    },

    error(name: string, meta?: Record<string, unknown>): void {
        _sink?.track({ type: 'error', name, ts: Date.now(), meta });
    },

    async flush(): Promise<void> {
        await Promise.resolve(_sink?.flush?.());
    },
};

// ── Test-only helper ──────────────────────────────────────────────────
/** Resets module-level state. Exported as `__resetForTests` to make the
 *  test contract explicit. Do not call from production code. */
export function __resetForTests(): void {
    _sink = null;
    _lastTab = null;
    _pageViewedAt = null;
}
