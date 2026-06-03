import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { formatAge, formatKind, renderTriageDashboard } from './TriageDashboard';
import type { DashboardSummary } from './TriageDashboard';
import { api } from '../../../../services/api.js';
import { store } from '../../../../services/store.js';
import { toast } from '../../../../services/toast.js';

// Mock api, store, and toast
vi.mock('../../../../services/api.js', () => ({
    api: {
        resetCircuitBreaker: vi.fn(),
    },
}));

vi.mock('../../../../services/store.js', () => ({
    store: {
        update: vi.fn(),
        state: {
            densityMode: 'overview',
        },
    },
}));

vi.mock('../../../../services/toast.js', () => ({
    toast: vi.fn(),
}));

describe('formatAge', () => {
    it('formats seconds nicely', () => {
        expect(formatAge(35)).toBe('35s ago');
        expect(formatAge(120)).toBe('2m ago');
        expect(formatAge(7200)).toBe('2h ago');
    });
});

describe('formatKind', () => {
    it('converts underscores to spaces and uppercases', () => {
        expect(formatKind('circuit_breaker_open')).toBe('CIRCUIT BREAKER OPEN');
    });
});

describe('renderTriageDashboard', () => {
    let container: HTMLElement;
    let triageQueue: HTMLElement;
    let doNextTasks: HTMLElement;
    let contextIncidentState: HTMLElement;
    let nowHealthBadge: HTMLElement;

    const mockSummary: DashboardSummary = {
        now: {
            health: 'degraded',
            uptime_seconds: 120,
            pool_size: 3,
            pool_healthy: 2,
            auth_mode: 'enabled',
            degradation_state: 'degraded',
            throughput_today: 15,
        },
        attention: [
            {
                id: 'cb:openai',
                kind: 'circuit_breaker_open',
                severity: 'critical',
                confidence: 1.0,
                blast_radius: 'endpoint:openai',
                age_sec: 10,
                baseline_delta: 'N/A',
                owner: 'circuit_breaker',
                suggested_actions: ['reset_cb', 'mute'],
                state: 'new',
            },
            {
                id: 'threat:ip:1.2.3.4',
                kind: 'high_threat_score',
                severity: 'warning',
                confidence: 0.8,
                blast_radius: 'ip:1.2.3.4',
                age_sec: 45,
                baseline_delta: 'N/A',
                owner: 'threat_ledger',
                suggested_actions: ['mute_actor', 'inspect_logs'],
                state: 'persistent',
            },
        ],
        do_next: [
            {
                id: 'task:reset_cb:openai',
                title: 'Reset circuit breaker for openai',
                description: 'Endpoint openai is offline.',
                action: 'reset_cb',
                target: 'openai',
            },
        ],
        recent_changes: [],
    };

    function makeStorage(initial: Record<string, string> = {}): Storage {
        const storeMap = new Map(Object.entries(initial));
        return {
            getItem: (k) => storeMap.get(k) ?? null,
            setItem: (k, v) => {
                storeMap.set(k, v);
            },
            removeItem: (k) => {
                storeMap.delete(k);
            },
            clear: () => storeMap.clear(),
            key: (i) => Array.from(storeMap.keys())[i] ?? null,
            get length() {
                return storeMap.size;
            },
        } as Storage;
    }

    let storage: Storage;

    beforeEach(() => {
        vi.useFakeTimers();
        container = document.createElement('div');
        
        triageQueue = document.createElement('div');
        triageQueue.id = 'triage-issues-queue';
        container.appendChild(triageQueue);

        doNextTasks = document.createElement('div');
        doNextTasks.id = 'do-next-tasks';
        container.appendChild(doNextTasks);

        contextIncidentState = document.createElement('span');
        contextIncidentState.id = 'context-incident-state';
        container.appendChild(contextIncidentState);

        nowHealthBadge = document.createElement('span');
        nowHealthBadge.id = 'now-health-badge';
        container.appendChild(nowHealthBadge);

        document.body.appendChild(container);
        
        storage = makeStorage();
        Object.defineProperty(globalThis, 'localStorage', { value: storage, configurable: true });
        
        vi.clearAllMocks();
    });

    afterEach(() => {
        vi.useRealTimers();
        container.remove();
    });

    it('renders empty placeholders when no issues', () => {
        const emptySummary: DashboardSummary = {
            now: {
                health: 'nominal',
                uptime_seconds: 120,
                pool_size: 3,
                pool_healthy: 3,
                auth_mode: 'enabled',
                degradation_state: 'nominal',
                throughput_today: 10,
            },
            attention: [],
            do_next: [],
            recent_changes: [],
        };

        renderTriageDashboard(emptySummary);

        expect(triageQueue.textContent).toContain('No urgent issues');
        expect(doNextTasks.textContent).toContain('No suggestions');
        expect(contextIncidentState.textContent).toContain('NOMINAL');
        expect(nowHealthBadge.textContent).toContain('NOMINAL');
    });

    it('renders active critical and warning alerts with appropriate badges', () => {
        renderTriageDashboard(mockSummary);

        expect(triageQueue.textContent).toContain('CIRCUIT BREAKER OPEN');
        expect(triageQueue.textContent).toContain('HIGH THREAT SCORE');
        expect(triageQueue.textContent).toContain('endpoint:openai');
        expect(triageQueue.textContent).toContain('ip:1.2.3.4');
        expect(contextIncidentState.textContent).toContain('ACTIVE ALERTS'); // Critical takes precedence
        expect(nowHealthBadge.textContent).toContain('DEGRADED');
    });

    it('acknowledges an issue and visual changes persist', async () => {
        renderTriageDashboard(mockSummary);

        const card = triageQueue.querySelector('[data-issue-id="cb:openai"]') as HTMLElement;
        expect(card).not.toBeNull();
        expect(card.className).not.toContain('opacity-40');

        const ackBtn = card.querySelector('button') as HTMLButtonElement;
        expect(ackBtn.textContent).toBe('Acknowledge');

        ackBtn.click();

        expect(card.className).toContain('opacity-40');
        expect(ackBtn.textContent).toBe('Acknowledged');
        expect(ackBtn.disabled).toBe(true);

        const stored = JSON.parse(localStorage.getItem('llmproxy:acknowledged_issues') || '[]');
        expect(stored).toContain('cb:openai');
    });

    it('mutes a category for 15 minutes', () => {
        const refreshSpy = vi.fn();
        renderTriageDashboard(mockSummary, refreshSpy);

        const card = triageQueue.querySelector('[data-issue-id="cb:openai"]') as HTMLElement;
        const muteBtn = Array.from(card.querySelectorAll('button')).find(
            (btn) => btn.textContent === 'Mute (15m)'
        ) as HTMLButtonElement;
        
        expect(muteBtn).not.toBeUndefined();
        muteBtn.click();

        const mutes = JSON.parse(localStorage.getItem('llmproxy:muted_issues') || '{}');
        expect(mutes['circuit_breaker_open']).toBeGreaterThan(Date.now());
        
        vi.runAllTimers();
        expect(refreshSpy).toHaveBeenCalled();
    });

    it('ignores corrupted acknowledged issue storage instead of breaking render', () => {
        storage = makeStorage({ 'llmproxy:acknowledged_issues': '{bad json' });
        Object.defineProperty(globalThis, 'localStorage', { value: storage, configurable: true });

        expect(() => renderTriageDashboard(mockSummary)).not.toThrow();
        expect(triageQueue.textContent).toContain('CIRCUIT BREAKER OPEN');
    });

    it('surfaces a warning when mute cannot be persisted', () => {
        storage = makeStorage();
        storage.setItem = vi.fn(() => {
            throw new Error('quota exceeded');
        });
        Object.defineProperty(globalThis, 'localStorage', { value: storage, configurable: true });

        const refreshSpy = vi.fn();
        renderTriageDashboard(mockSummary, refreshSpy);

        const card = triageQueue.querySelector('[data-issue-id="cb:openai"]') as HTMLElement;
        const muteBtn = Array.from(card.querySelectorAll('button')).find(
            (btn) => btn.textContent === 'Mute (15m)'
        ) as HTMLButtonElement;

        muteBtn.click();

        expect(toast).toHaveBeenCalledWith('Unable to mute this alert category', 'warning');
        vi.runAllTimers();
        expect(refreshSpy).not.toHaveBeenCalled();
    });

    it('navigates to the relevant tab on Inspect', () => {
        renderTriageDashboard(mockSummary);

        const card = triageQueue.querySelector('[data-issue-id="cb:openai"]') as HTMLElement;
        const inspectBtn = Array.from(card.querySelectorAll('button')).find(
            (btn) => btn.textContent === 'Inspect'
        ) as HTMLButtonElement;

        expect(inspectBtn).not.toBeUndefined();
        inspectBtn.click();

        expect(store.update).toHaveBeenCalledWith({ currentTab: 'endpoints' });
    });

    it('resets a circuit breaker when CB Reset clicked', async () => {
        vi.mocked(api.resetCircuitBreaker).mockResolvedValueOnce({ ok: true });
        
        const refreshSpy = vi.fn();
        renderTriageDashboard(mockSummary, refreshSpy);

        const card = triageQueue.querySelector('[data-issue-id="cb:openai"]') as HTMLElement;
        const resetBtn = Array.from(card.querySelectorAll('button')).find(
            (btn) => btn.textContent === 'Reset CB'
        ) as HTMLButtonElement;

        expect(resetBtn).not.toBeUndefined();
        resetBtn.click();

        expect(api.resetCircuitBreaker).toHaveBeenCalledWith('openai');
        
        // Wait for promise resolution
        await vi.runAllTimersAsync();
        expect(refreshSpy).toHaveBeenCalled();
    });

    it('executes a Do Next task to reset a CB', async () => {
        vi.mocked(api.resetCircuitBreaker).mockResolvedValueOnce({ ok: true });

        const refreshSpy = vi.fn();
        renderTriageDashboard(mockSummary, refreshSpy);

        const ctaBtn = doNextTasks.querySelector('button') as HTMLButtonElement;
        expect(ctaBtn.textContent).toBe('RESET');

        ctaBtn.click();

        expect(api.resetCircuitBreaker).toHaveBeenCalledWith('openai');
        
        // Wait for promise resolution
        await vi.runAllTimersAsync();
        expect(refreshSpy).toHaveBeenCalled();
    });
});
