import { cx } from '../../../ui';
import { api } from '../../../../services/api.js';
import { store } from '../../../../services/store.js';
import { toast } from '../../../../services/toast.js';

export interface TriageIssue {
    id: string;
    kind: string;
    severity: 'critical' | 'warning';
    confidence: number;
    blast_radius: string;
    age_sec: number;
    baseline_delta: string;
    owner: string;
    suggested_actions: string[];
    state: 'new' | 'persistent';
}

export interface DoNextTask {
    id: string;
    title: string;
    description: string;
    action: string;
    target: string;
}

export interface RecentChange {
    timestamp: number;
    type: string;
    description: string;
}

export interface DashboardSummary {
    now: {
        health: 'nominal' | 'degraded' | 'critical';
        uptime_seconds: number;
        pool_size: number;
        pool_healthy: number;
        auth_mode: 'enabled' | 'disabled';
        degradation_state: 'nominal' | 'degraded' | 'critical';
        throughput_today: number;
    };
    attention: TriageIssue[];
    do_next: DoNextTask[];
    recent_changes: RecentChange[];
}

/** Helper to format age_sec nicely. */
export function formatAge(seconds: number): string {
    if (seconds < 60) return `${seconds}s ago`;
    const mins = Math.floor(seconds / 60);
    if (mins < 60) return `${mins}m ago`;
    const hours = Math.floor(mins / 60);
    return `${hours}h ago`;
}

/** Helper to format kind header text. */
export function formatKind(kind: string): string {
    return kind.replace(/_/g, ' ').toUpperCase();
}

/**
 * Renders the triage dashboard zones: Needs Attention, Do Next, and global context bar.
 */
export function renderTriageDashboard(summary: DashboardSummary, onRefreshRequest?: () => void): void {
    // 1. Update Global Context Bar - Incident State
    const incidentStateSpan = document.getElementById('context-incident-state');
    if (incidentStateSpan) {
        incidentStateSpan.replaceChildren();
        const activeMutes = getActiveMutes();
        // Filter out muted issues from context bar considerations
        const activeCriticalCount = summary.attention.filter(
            (item) => item.severity === 'critical' && !activeMutes.has(item.kind)
        ).length;
        const activeWarningCount = summary.attention.filter(
            (item) => item.severity === 'warning' && !activeMutes.has(item.kind)
        ).length;

        const dot = document.createElement('span');
        dot.className = 'w-1 h-1 rounded-full animate-pulse';

        if (activeCriticalCount > 0) {
            incidentStateSpan.className =
                'flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-rose-500/10 border border-rose-500/20 text-[10px] text-rose-400 font-mono';
            dot.className += ' bg-rose-400';
            incidentStateSpan.appendChild(dot);
            incidentStateSpan.append('ACTIVE ALERTS');
        } else if (activeWarningCount > 0) {
            incidentStateSpan.className =
                'flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-amber-500/10 border border-amber-500/20 text-[10px] text-amber-400 font-mono';
            dot.className += ' bg-amber-400';
            incidentStateSpan.appendChild(dot);
            incidentStateSpan.append('DEGRADED');
        } else {
            incidentStateSpan.className =
                'flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-emerald-500/10 border border-emerald-500/20 text-[10px] text-emerald-400 font-mono';
            dot.className += ' bg-emerald-400';
            incidentStateSpan.appendChild(dot);
            incidentStateSpan.append('NOMINAL');
        }
    }

    // 2. Update Zone 1 Health Badge
    const healthBadge = document.getElementById('now-health-badge');
    if (healthBadge) {
        const state = summary.now.health;
        healthBadge.textContent = state.toUpperCase();
        if (state === 'critical') {
            healthBadge.className =
                'text-[10px] font-mono text-rose-400 bg-rose-500/10 px-2 py-0.5 rounded border border-rose-500/20';
        } else if (state === 'degraded') {
            healthBadge.className =
                'text-[10px] font-mono text-amber-400 bg-amber-500/10 px-2 py-0.5 rounded border border-amber-500/20';
        } else {
            healthBadge.className =
                'text-[10px] font-mono text-emerald-400 bg-emerald-500/10 px-2 py-0.5 rounded border border-emerald-500/20';
        }
    }

    // 3. Render Zone 2: Needs Attention
    renderNeedsAttention(summary.attention, onRefreshRequest);

    // 4. Render Zone 3: Do Next
    renderDoNext(summary.do_next, onRefreshRequest);
}

function getActiveMutes(): Set<string> {
    const active = new Set<string>();
    try {
        const mutes = JSON.parse(localStorage.getItem('llmproxy:muted_issues') || '{}');
        const now = Date.now();
        for (const key of Object.keys(mutes)) {
            if (mutes[key] > now) {
                active.add(key);
            }
        }
    } catch {
        /* ignore invalid storage */
    }
    return active;
}

function renderNeedsAttention(issues: TriageIssue[], onRefreshRequest?: () => void): void {
    const queue = document.getElementById('triage-issues-queue');
    const badge = document.getElementById('triage-count-badge');
    if (!queue) return;

    queue.replaceChildren();

    // Fetch muted/acknowledged metadata
    const activeMutes = getActiveMutes();
    let acknowledged: string[] = [];
    try {
        acknowledged = JSON.parse(localStorage.getItem('llmproxy:acknowledged_issues') || '[]');
    } catch {
        acknowledged = [];
    }
    const ackSet = new Set(acknowledged);

    // Filter out muted categories
    const visibleIssues = issues.filter((item) => !activeMutes.has(item.kind));

    // Update count badge
    if (badge) {
        badge.textContent = `${visibleIssues.length} Issue${visibleIssues.length !== 1 ? 's' : ''}`;
    }

    if (visibleIssues.length === 0) {
        const empty = document.createElement('p');
        empty.className = 'text-[11px] text-slate-500 italic py-2';
        empty.textContent = 'No urgent issues. System is running nominal.';
        queue.appendChild(empty);
        return;
    }

    visibleIssues.forEach((item) => {
        const card = document.createElement('div');
        const isAck = ackSet.has(item.id);

        card.className = cx(
            'flex flex-col bg-white/[0.02] border border-white/[0.05] rounded-xl p-3 gap-2 transition-all duration-300',
            isAck ? 'opacity-40 line-through' : 'hover:bg-white/[0.04]'
        );
        card.setAttribute('data-issue-id', item.id);

        // Header Row: Severity Badge + Blast Radius Badge + Indicators
        const header = document.createElement('div');
        header.className = 'flex items-center justify-between';

        const badgesLeft = document.createElement('div');
        badgesLeft.className = 'flex items-center gap-1.5';

        // Severity
        const sevBadge = document.createElement('span');
        sevBadge.className = cx(
            'text-[9px] font-bold font-mono px-1.5 py-0.5 rounded border uppercase',
            item.severity === 'critical'
                ? 'bg-rose-500/10 text-rose-400 border-rose-500/20'
                : 'bg-amber-500/10 text-amber-400 border-amber-500/20'
        );
        sevBadge.textContent = item.severity;
        badgesLeft.appendChild(sevBadge);

        // Blast radius
        const blastBadge = document.createElement('span');
        blastBadge.className =
            'text-[9px] font-mono px-1.5 py-0.5 rounded bg-sky-500/10 text-sky-400 border border-sky-500/20';
        blastBadge.textContent = item.blast_radius;
        badgesLeft.appendChild(blastBadge);

        // State indicator: 'new' vs 'persistent'
        if (item.state === 'new') {
            const stateDot = document.createElement('span');
            stateDot.className =
                'flex items-center gap-1 text-[9px] font-mono text-emerald-400 bg-emerald-500/10 px-1.5 py-0.5 rounded';
            const dot = document.createElement('span');
            dot.className = 'w-1 h-1 rounded-full bg-emerald-400 animate-ping';
            stateDot.appendChild(dot);
            stateDot.append('NEW');
            badgesLeft.appendChild(stateDot);
        }

        header.appendChild(badgesLeft);

        // Info: Age + Confidence
        const infoRight = document.createElement('div');
        infoRight.className = 'text-[9px] font-mono text-slate-500 flex items-center gap-2';

        const ageSpan = document.createElement('span');
        ageSpan.textContent = formatAge(item.age_sec);
        infoRight.appendChild(ageSpan);

        if (item.baseline_delta && item.baseline_delta !== 'N/A') {
            const confSpan = document.createElement('span');
            confSpan.className = 'text-rose-400 bg-rose-500/10 px-1 rounded';
            confSpan.textContent = item.baseline_delta;
            infoRight.appendChild(confSpan);
        } else {
            const confSpan = document.createElement('span');
            confSpan.textContent = `Conf: ${(item.confidence * 100).toFixed(0)}%`;
            infoRight.appendChild(confSpan);
        }

        header.appendChild(infoRight);
        card.appendChild(header);

        // Title and Description
        const content = document.createElement('div');
        content.className = 'flex flex-col';
        const title = document.createElement('h3');
        title.className = 'text-[11px] font-bold text-white tracking-tight';
        title.textContent = formatKind(item.kind);
        content.appendChild(title);
        card.appendChild(content);

        // Footer Actions Row
        const actionsRow = document.createElement('div');
        actionsRow.className = 'flex items-center justify-end gap-1.5 mt-1 border-t border-white/[0.02] pt-1.5';

        // 1. Acknowledge Action
        const ackBtn = document.createElement('button');
        ackBtn.type = 'button';
        ackBtn.className =
            'px-2 py-1 rounded text-[9px] font-bold transition-all bg-white/5 border border-white/10 text-slate-300 hover:bg-white/10';
        ackBtn.textContent = isAck ? 'Acknowledged' : 'Acknowledge';
        ackBtn.disabled = isAck;
        ackBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            if (ackSet.has(item.id)) return;
            ackSet.add(item.id);
            localStorage.setItem('llmproxy:acknowledged_issues', JSON.stringify(Array.from(ackSet)));
            card.classList.add('opacity-40', 'line-through');
            ackBtn.textContent = 'Acknowledged';
            ackBtn.disabled = true;
            toast('Alert acknowledged', 'info');
        });
        actionsRow.appendChild(ackBtn);

        // 2. Mute Category Action
        const muteBtn = document.createElement('button');
        muteBtn.type = 'button';
        muteBtn.className =
            'px-2 py-1 rounded text-[9px] font-bold transition-all bg-white/5 border border-white/10 text-slate-300 hover:bg-white/10';
        muteBtn.textContent = 'Mute (15m)';
        muteBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            try {
                const mutes = JSON.parse(localStorage.getItem('llmproxy:muted_issues') || '{}');
                mutes[item.kind] = Date.now() + 15 * 60 * 1000;
                localStorage.setItem('llmproxy:muted_issues', JSON.stringify(mutes));
                toast(`Muted alerts of type ${formatKind(item.kind)} for 15m`, 'info');
                // Animate out card
                card.style.transform = 'scale(0.95)';
                card.style.opacity = '0';
                setTimeout(() => {
                    if (onRefreshRequest) onRefreshRequest();
                }, 300);
            } catch {
                toast('Unable to mute this alert category', 'warning');
            }
        });
        actionsRow.appendChild(muteBtn);

        // 3. Inspect Link
        const inspectBtn = document.createElement('button');
        inspectBtn.type = 'button';
        inspectBtn.className =
            'px-2 py-1 rounded text-[9px] font-bold transition-all bg-sky-500/10 border border-sky-500/20 text-sky-400 hover:bg-sky-500/20';
        inspectBtn.textContent = 'Inspect';
        inspectBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            let targetTab = 'threats';
            if (item.kind.startsWith('circuit_breaker')) {
                targetTab = 'endpoints';
            } else if (item.kind.startsWith('actor') || item.kind.startsWith('high_threat')) {
                targetTab = 'security';
            } else if (item.kind === 'empty_registry') {
                targetTab = 'endpoints';
            } else if (item.kind.startsWith('budget')) {
                targetTab = 'analytics';
            }
            store.update({ currentTab: targetTab });
        });
        actionsRow.appendChild(inspectBtn);

        // 4. Circuit Breaker Quick Action (Reset)
        if (item.kind.startsWith('circuit_breaker') && item.suggested_actions.includes('reset_cb')) {
            const resetBtn = document.createElement('button');
            resetBtn.type = 'button';
            resetBtn.className =
                'px-2 py-1 rounded text-[9px] font-bold transition-all bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 hover:bg-emerald-500/20';
            resetBtn.textContent = 'Reset CB';
            resetBtn.addEventListener('click', async (e) => {
                e.stopPropagation();
                const epId = item.blast_radius.replace('endpoint:', '');
                resetBtn.disabled = true;
                resetBtn.textContent = 'Resetting...';
                try {
                    await api.resetCircuitBreaker(epId);
                    toast(`Circuit breaker for ${epId} reset successful`, 'success');
                    if (onRefreshRequest) onRefreshRequest();
                } catch (err) {
                    toast(`Failed to reset breaker: ${(err as Error).message}`, 'error');
                    resetBtn.disabled = false;
                    resetBtn.textContent = 'Reset CB';
                }
            });
            actionsRow.appendChild(resetBtn);
        }

        card.appendChild(actionsRow);
        queue.appendChild(card);
    });
}

function renderDoNext(tasks: DoNextTask[], onRefreshRequest?: () => void): void {
    const doNextContainer = document.getElementById('do-next-tasks');
    if (!doNextContainer) return;

    doNextContainer.replaceChildren();

    if (tasks.length === 0) {
        const empty = document.createElement('p');
        empty.className = 'text-[11px] text-slate-500 italic py-2';
        empty.textContent = 'No suggestions. All system routines nominal.';
        doNextContainer.appendChild(empty);
        return;
    }

    tasks.forEach((task) => {
        const row = document.createElement('div');
        row.className =
            'flex items-start justify-between bg-white/[0.01] border border-white/[0.03] rounded-xl p-3 gap-3 hover:bg-white/[0.03] transition-colors';

        const content = document.createElement('div');
        content.className = 'flex-1 min-w-0';

        const title = document.createElement('h4');
        title.className = 'text-[11px] font-bold text-white truncate';
        title.textContent = task.title;
        content.appendChild(title);

        const desc = document.createElement('p');
        desc.className = 'text-[10px] text-slate-400 mt-0.5 leading-normal';
        desc.textContent = task.description;
        content.appendChild(desc);

        row.appendChild(content);

        // CTA Button
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'shrink-0 px-2 py-1 rounded text-[9px] font-bold font-mono transition-all';

        if (task.action === 'reset_cb') {
            btn.className += ' bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 hover:bg-emerald-500/20';
            btn.textContent = 'RESET';
            btn.addEventListener('click', async (e) => {
                e.stopPropagation();
                btn.disabled = true;
                btn.textContent = 'WAIT';
                try {
                    await api.resetCircuitBreaker(task.target);
                    toast(`Breaker ${task.target} is now CLOSED`, 'success');
                    if (onRefreshRequest) onRefreshRequest();
                } catch (err) {
                    toast(`Reset failed: ${(err as Error).message}`, 'error');
                    btn.disabled = false;
                    btn.textContent = 'RESET';
                }
            });
        } else {
            btn.className += ' bg-sky-500/10 border border-sky-500/20 text-sky-400 hover:bg-sky-500/20';
            btn.textContent = 'GO';
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                let targetTab = 'threats';
                if (task.action === 'inspect_logs') {
                    targetTab = 'logs';
                } else if (task.action === 'add_endpoint') {
                    targetTab = 'endpoints';
                } else if (task.action === 'increase_limit') {
                    targetTab = 'settings';
                } else if (task.action === 'view_analytics') {
                    targetTab = 'analytics';
                } else if (task.action === 'view_plugins') {
                    targetTab = 'plugins';
                }
                store.update({ currentTab: targetTab });
            });
        }

        row.appendChild(btn);
        doNextContainer.appendChild(row);
    });
}
