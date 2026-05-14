import { createBadge, createButton, createEmptyState, createErrorState, createSkeleton, cx } from '../../ui';
import type { BadgeIntent } from '../../ui';
import { rum } from '../../services/rum';
import type { EventFeedStatus, SecurityEvent } from './types';

const MUTE_STORAGE_KEY = 'llmproxy:muted-threats';
const MAX_EVENTS = 50;

const LEVEL_INTENT: Record<string, BadgeIntent> = {
    CRITICAL: 'danger',
    SECURITY: 'primary',
    ERROR: 'danger',
    WARNING: 'warning',
    INFO: 'info',
};

const ICON_INVESTIGATE =
    '<svg viewBox="0 0 16 16" width="11" height="11" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">' +
    '<circle cx="7" cy="7" r="4.5"/><path d="M10.4 10.4 L13.5 13.5"/></svg>';
const ICON_EXPLAIN =
    '<svg viewBox="0 0 16 16" width="11" height="11" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">' +
    '<circle cx="8" cy="8" r="6.5"/><path d="M6 6a2 2 0 1 1 2.5 1.9c-0.5.2-0.5.6-0.5 1.1V10"/><circle cx="8" cy="12" r="0.4" fill="currentColor"/></svg>';
const ICON_MUTED =
    '<svg viewBox="0 0 16 16" width="11" height="11" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">' +
    '<path d="M3 6h2l3-2.5v9L5 10H3z"/><path d="M11 6l3 4M14 6l-3 4"/></svg>';
const ICON_MUTE =
    '<svg viewBox="0 0 16 16" width="11" height="11" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">' +
    '<path d="M3 6h2l3-2.5v9L5 10H3z"/><path d="M11 5.5C12.2 6.5 12.2 9.5 11 10.5"/></svg>';

/** Pure helper — derives a stable mute key for an event. Exported for tests. */
export function muteKeyFor(event: SecurityEvent): string {
    const level = (event.level || 'INFO').toUpperCase();
    if (event.signature) return `${level}:${event.signature}`;
    const msg = (event.message || '').slice(0, 32).trim();
    return `${level}:${msg}`;
}

/** Pure helper — returns whether the event is "security-relevant". Exported for tests. */
export function isSecurityEvent(event: SecurityEvent): boolean {
    const level = (event.level || '').toUpperCase();
    if (level === 'SECURITY' || level === 'WARNING' || level === 'ERROR' || level === 'CRITICAL') return true;
    const msg = (event.message || '').toUpperCase();
    return /\b(SHIELD|BLOCK|INJECT|PII|FIREWALL|AUTH|RATE|ZT|PANIC|BUDGET)\b/.test(msg);
}

interface EventFeedDeps {
    /** Override the SSE constructor — used by tests to inject a fake. */
    eventSourceFactory?: (url: string) => EventSource;
    /** Read the auth token. Defaults to localStorage. */
    getToken?: () => string;
    /** Storage backend, defaults to localStorage. */
    storage?: Storage;
    /** Time window the feed claims to cover, surfaced in the empty state. */
    windowLabel?: string;
}

export class ThreatEventFeed {
    private readonly container: HTMLElement;
    private readonly listEl: HTMLElement;
    private readonly statusEl: HTMLElement;
    private readonly deps: Required<Omit<EventFeedDeps, 'eventSourceFactory'>> &
        Pick<EventFeedDeps, 'eventSourceFactory'>;

    private status: EventFeedStatus = 'idle';
    private es: EventSource | null = null;
    private errorCount = 0;
    private events: SecurityEvent[] = [];
    private muted: Set<string>;
    private retryTimer: ReturnType<typeof setTimeout> | null = null;
    private destroyed = false;

    constructor(container: HTMLElement, deps: EventFeedDeps = {}) {
        this.container = container;
        this.deps = {
            eventSourceFactory: deps.eventSourceFactory,
            getToken:
                deps.getToken ??
                (() => (typeof localStorage !== 'undefined' ? (localStorage.getItem('proxy_key') ?? '') : '')),
            storage:
                deps.storage ??
                (typeof localStorage !== 'undefined' ? localStorage : (undefined as unknown as Storage)),
            windowLabel: deps.windowLabel ?? 'live',
        };
        this.muted = this.loadMuted();

        const wrap = document.createElement('div');
        wrap.className = 'bg-white/[0.03] backdrop-blur-xl rounded-2xl border border-white/[0.06] p-6';

        const header = document.createElement('div');
        header.className = 'flex items-center justify-between mb-4';
        const title = document.createElement('h2');
        title.className = 'text-xs font-bold text-white';
        title.textContent = 'Recent Security Events';
        header.appendChild(title);

        this.statusEl = document.createElement('div');
        this.statusEl.className = 'flex items-center gap-2 text-[10px] font-mono text-slate-500';
        header.appendChild(this.statusEl);

        wrap.appendChild(header);

        this.listEl = document.createElement('div');
        this.listEl.className = 'space-y-2 max-h-64 overflow-y-auto';
        this.listEl.setAttribute('role', 'log');
        this.listEl.setAttribute('aria-live', 'polite');
        this.listEl.setAttribute('aria-label', 'Security event stream');
        this.listEl.setAttribute('data-testid', 'threat-feed-list');
        wrap.appendChild(this.listEl);

        container.replaceChildren(wrap);

        this.renderStatus();
        this.renderList();
    }

    connect(): void {
        if (this.destroyed) return;
        const token = this.deps.getToken();
        if (!token) {
            this.setStatus('unauthenticated');
            // Soft retry while the user authenticates
            this.retryTimer = setTimeout(() => this.connect(), 2_000);
            return;
        }

        this.setStatus(this.errorCount > 0 ? 'reconnecting' : 'connecting');
        try {
            const url = `${typeof window !== 'undefined' ? window.location.origin : ''}/api/v1/logs?token=${encodeURIComponent(token)}`;
            const factory = this.deps.eventSourceFactory ?? ((u: string) => new EventSource(u));
            if (this.es) this.es.close();
            this.es = factory(url);

            this.es.onopen = () => {
                this.errorCount = 0;
                this.setStatus('streaming');
            };
            this.es.onmessage = (ev: MessageEvent) => {
                this.errorCount = 0;
                try {
                    const entry = JSON.parse(ev.data) as SecurityEvent;
                    if (!isSecurityEvent(entry)) return;
                    this.addEvent(entry);
                } catch {
                    /* drop invalid JSON */
                }
            };
            this.es.onerror = () => {
                this.errorCount++;
                if (this.errorCount > 5) {
                    this.es?.close();
                    this.es = null;
                    this.setStatus('error');
                }
            };
        } catch (err) {
            this.setStatus('error');
            this.renderListError((err as Error)?.message);
        }
    }

    disconnect(): void {
        this.destroyed = true;
        if (this.retryTimer) clearTimeout(this.retryTimer);
        if (this.es) {
            this.es.close();
            this.es = null;
        }
        this.setStatus('idle');
    }

    /** Inject an event from outside the SSE source — used by tests and for replays. */
    addEvent(entry: SecurityEvent): void {
        this.events.unshift(entry);
        if (this.events.length > MAX_EVENTS) this.events.length = MAX_EVENTS;
        this.renderList();
        // Dispatch a global hook so legacy renderers (e.g. the threat chart) can react.
        if (typeof window !== 'undefined') {
            window.dispatchEvent(new CustomEvent('llmproxy:threat-event', { detail: entry }));
        }
    }

    /** Toggle the mute state for an event's category. */
    toggleMute(event: SecurityEvent): void {
        const key = muteKeyFor(event);
        const next = !this.muted.has(key);
        if (next) this.muted.add(key);
        else this.muted.delete(key);
        rum.action('threat_mute_toggle', { key, muted: next });
        this.persistMuted();
        this.renderList();
    }

    /** Returns true when the event is in the muted set. */
    isMuted(event: SecurityEvent): boolean {
        return this.muted.has(muteKeyFor(event));
    }

    getMutedKeys(): string[] {
        return Array.from(this.muted);
    }

    /* — internals — */

    private setStatus(status: EventFeedStatus): void {
        const prev: EventFeedStatus = this.status;
        this.status = status;
        this.renderStatus();
        // Status transitions that change what the list shows (loading/empty/error)
        // need a re-render. Streaming/idle don't.
        const shouldRerender =
            status === 'error' || prev === 'error' || (status === 'connecting' && this.events.length === 0);
        if (shouldRerender) this.renderList();
    }

    private loadMuted(): Set<string> {
        try {
            const raw = this.deps.storage?.getItem(MUTE_STORAGE_KEY);
            if (!raw) return new Set();
            const arr = JSON.parse(raw);
            return Array.isArray(arr) ? new Set(arr.map(String)) : new Set();
        } catch {
            return new Set();
        }
    }

    private persistMuted(): void {
        try {
            this.deps.storage?.setItem(MUTE_STORAGE_KEY, JSON.stringify(Array.from(this.muted)));
        } catch {
            /* quota or privacy mode — silently degrade */
        }
    }

    private renderStatus(): void {
        // O.2: pulse the dot on `streaming` (live, healthy) and `connecting`
        // (still establishing) — the SSE feed is "actively in motion".
        // reconnecting stays still (warning intent) so it reads as alarm,
        // not heartbeat.
        const map: Record<EventFeedStatus, { label: string; intent: BadgeIntent; dot?: boolean; pulse?: boolean }> = {
            idle: { label: 'idle', intent: 'neutral' },
            connecting: { label: 'connecting', intent: 'info', dot: true, pulse: true },
            streaming: { label: this.deps.windowLabel, intent: 'success', dot: true, pulse: true },
            error: { label: 'disconnected', intent: 'danger' },
            reconnecting: { label: 'reconnecting', intent: 'warning', dot: true },
            unauthenticated: { label: 'awaiting auth', intent: 'neutral' },
        };
        const cfg = map[this.status];
        this.statusEl.replaceChildren(
            createBadge({ label: cfg.label, intent: cfg.intent, dot: cfg.dot, pulse: cfg.pulse, size: 'sm' })
        );
    }

    private visibleEvents(): { events: SecurityEvent[]; mutedCount: number } {
        let mutedCount = 0;
        const visible: SecurityEvent[] = [];
        for (const ev of this.events) {
            if (this.isMuted(ev)) {
                mutedCount++;
                continue;
            }
            visible.push(ev);
        }
        return { events: visible, mutedCount };
    }

    private renderList(): void {
        if (this.status === 'error') return this.renderListError();
        if (this.status === 'connecting' && this.events.length === 0) return this.renderListLoading();

        const { events, mutedCount } = this.visibleEvents();

        if (events.length === 0) {
            this.listEl.replaceChildren(
                createEmptyState({
                    title: 'No security events yet',
                    description:
                        this.events.length > 0
                            ? `${mutedCount} muted. Adjust filters or unmute below.`
                            : 'When the WAF or guards block a request, it lands here in real time.',
                    testId: 'threat-feed-empty',
                })
            );
            return;
        }

        const fragment = document.createDocumentFragment();
        for (const ev of events) {
            fragment.appendChild(this.renderRow(ev));
        }
        if (mutedCount > 0) {
            const note = document.createElement('p');
            note.className = 'text-[10px] font-mono text-slate-500 pt-2 border-t border-white/[0.04]';
            note.textContent = `${mutedCount} muted event${mutedCount === 1 ? '' : 's'} hidden — clear in event row to unmute.`;
            fragment.appendChild(note);
        }
        this.listEl.replaceChildren(fragment);
    }

    private renderListLoading(): void {
        this.listEl.replaceChildren(createSkeleton({ shape: 'block', height: '3rem', repeat: 3, gap: 'gap-2' }));
    }

    private renderListError(detail?: string): void {
        this.listEl.replaceChildren(
            createErrorState({
                title: 'Event stream disconnected',
                description: 'Lost contact with the live security feed.',
                detail,
                onRetry: () => {
                    this.errorCount = 0;
                    this.connect();
                },
                retryLabel: 'Reconnect',
                testId: 'threat-feed-error',
            })
        );
    }

    private renderRow(ev: SecurityEvent): HTMLElement {
        const level = (ev.level || 'INFO').toUpperCase();
        const intent = LEVEL_INTENT[level] ?? 'info';
        const isMuted = this.isMuted(ev);

        const row = document.createElement('div');
        row.className = cx(
            'flex items-start gap-3 p-3 rounded-xl border transition-colors',
            'border-white/[0.06] bg-white/[0.02] hover:bg-white/[0.04]',
            isMuted && 'opacity-50'
        );
        row.setAttribute('data-mute-key', muteKeyFor(ev));

        const ts = document.createElement('span');
        ts.className = 'text-[9px] font-mono text-slate-500 shrink-0 mt-0.5 w-16';
        ts.textContent = ev.timestamp || '--:--';
        row.appendChild(ts);

        const levelBadge = createBadge({ label: level, intent, size: 'sm', className: 'shrink-0 mt-0.5' });
        row.appendChild(levelBadge);

        const msgWrap = document.createElement('div');
        msgWrap.className = 'flex-1 min-w-0';
        const msg = document.createElement('p');
        msg.className = 'text-[11px] font-mono text-slate-200 break-words';
        msg.textContent = ev.message || '';
        msgWrap.appendChild(msg);

        if (ev.signature) {
            const sig = document.createElement('p');
            sig.className = 'text-[9px] font-mono text-slate-500 mt-0.5';
            sig.textContent = `rule: ${ev.signature}`;
            msgWrap.appendChild(sig);
        }
        row.appendChild(msgWrap);

        const actions = document.createElement('div');
        actions.className = 'flex items-center gap-1 shrink-0';

        if (ev.req_id) {
            const investigate = createButton({
                label: 'Investigate',
                size: 'sm',
                variant: 'ghost',
                icon: ICON_INVESTIGATE,
                ariaLabel: `Investigate request ${ev.req_id}`,
                testId: `threat-investigate-${ev.req_id}`,
            });
            investigate.dataset.drilldown = `request:${ev.req_id}`;
            actions.appendChild(investigate);
        }

        if (ev.signature) {
            const explain = createButton({
                label: 'Explain',
                size: 'sm',
                variant: 'ghost',
                icon: ICON_EXPLAIN,
                ariaLabel: `Explain rule ${ev.signature}`,
                testId: `threat-explain-${ev.signature}`,
            });
            explain.dataset.explain = `rule:${ev.signature}`;
            actions.appendChild(explain);
        }

        const muteBtn = createButton({
            label: isMuted ? 'Unmute' : 'Mute',
            size: 'sm',
            variant: 'ghost',
            icon: isMuted ? ICON_MUTED : ICON_MUTE,
            pressed: isMuted,
            ariaLabel: isMuted ? `Unmute ${level}` : `Mute future ${level} events like this`,
            onClick: () => this.toggleMute(ev),
            testId: `threat-mute-${muteKeyFor(ev)}`,
        });
        actions.appendChild(muteBtn);

        row.appendChild(actions);
        return row;
    }
}
