import { cx } from '../../../ui';
import type { GuardsStatus } from './types';

export interface BudgetInputs {
    /** Sum of llm_proxy_cost_total counter (USD). */
    cost: number;
    /** llm_proxy_budget_consumed_usd from /metrics. */
    consumed: number;
    /** llm_proxy_budget_limit_usd from /metrics. */
    limit: number;
    /** Optional override + canonical daily_limit from /api/v1/guards/status. */
    guardsStatus?: GuardsStatus | null;
}

interface Computed {
    consumed: number;
    limit: number;
    pct: number; // 0–100 when limit > 0; 0 otherwise
    color: 'rose' | 'amber' | 'emerald';
    remaining: number | null;
}

/** Pure helper exported for tests. */
export function computeBudget(inputs: BudgetInputs): Computed | { configured: false } {
    let { consumed, limit } = inputs;
    if (inputs.guardsStatus?.budget) {
        consumed = inputs.guardsStatus.budget.total_cost_today ?? consumed;
        if (limit <= 0 && (inputs.guardsStatus.budget.daily_limit ?? 0) > 0) {
            limit = inputs.guardsStatus.budget.daily_limit ?? 0;
        }
    }
    if (limit <= 0 && inputs.cost <= 0 && consumed <= 0) {
        return { configured: false };
    }
    const pct = limit > 0 ? Math.min((consumed / limit) * 100, 100) : 0;
    const color = pct > 80 ? 'rose' : pct > 50 ? 'amber' : 'emerald';
    return {
        consumed,
        limit,
        pct,
        color,
        remaining: limit > 0 ? limit - consumed : null,
    };
}

const COLOR_TEXT = {
    rose: 'text-rose-400',
    amber: 'text-amber-400',
    emerald: 'text-emerald-400',
} as const;

const COLOR_BAR = {
    rose: 'bg-rose-500/60',
    amber: 'bg-amber-500/60',
    emerald: 'bg-emerald-500/60',
} as const;

export function renderBudgetGauge(container: HTMLElement, inputs: BudgetInputs): void {
    const result = computeBudget(inputs);
    container.replaceChildren();
    container.dataset.ready = '1';
    container.setAttribute('data-testid', 'budget-gauge');

    if ('configured' in result) {
        const empty = document.createElement('div');
        empty.className = 'text-[9px] text-slate-600 font-mono';
        empty.append('No budget configured — set ');
        const code = document.createElement('code');
        code.className = 'text-slate-500';
        code.textContent = 'budget.daily_limit';
        empty.appendChild(code);
        empty.append(' in config.yaml');
        container.appendChild(empty);
        return;
    }

    const head = document.createElement('div');
    head.className = 'flex items-center justify-between mb-2';

    const left = document.createElement('div');
    const consumedSpan = document.createElement('span');
    consumedSpan.className = 'text-lg font-black font-mono text-white';
    consumedSpan.textContent = `$${result.consumed.toFixed(4)}`;
    left.appendChild(consumedSpan);
    if (result.limit > 0) {
        const limitSpan = document.createElement('span');
        limitSpan.className = 'text-[10px] text-slate-500';
        limitSpan.textContent = ` / $${result.limit.toFixed(2)}`;
        left.appendChild(limitSpan);
    }
    head.appendChild(left);

    const right = document.createElement('span');
    right.className = cx('text-[9px] font-mono', COLOR_TEXT[result.color]);
    right.textContent = result.limit > 0 ? `${result.pct.toFixed(0)}% used` : 'tracking';
    head.appendChild(right);

    container.appendChild(head);

    if (result.limit > 0) {
        const track = document.createElement('div');
        track.className = 'w-full h-2 bg-white/5 rounded-full overflow-hidden';
        const fill = document.createElement('div');
        fill.className = cx('h-full rounded-full transition-all', COLOR_BAR[result.color]);
        fill.style.width = `${result.pct}%`;
        fill.setAttribute('data-testid', 'budget-gauge-bar');
        track.appendChild(fill);
        container.appendChild(track);

        const foot = document.createElement('div');
        foot.className = 'flex justify-between mt-1';
        const remainSpan = document.createElement('span');
        remainSpan.className = 'text-[10px] text-slate-600 font-mono';
        remainSpan.textContent = `$${(result.remaining ?? 0).toFixed(2)} remaining`;
        const resetSpan = document.createElement('span');
        resetSpan.className = 'text-[10px] text-slate-600 font-mono';
        resetSpan.textContent = 'Daily reset';
        foot.appendChild(remainSpan);
        foot.appendChild(resetSpan);
        container.appendChild(foot);
    }
}
