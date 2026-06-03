/**
 * Settings → Health (P.3)
 *
 * Renders the per-component status block from M.3's GET /health into a
 * grid of tiles so an operator sees which subsystem is degrading
 * before the proxy as a whole tips. The health endpoint always
 * returns 200; body.status carries the actual roll-up.
 */

import { createBadge, createErrorState, createSkeleton, cx } from '../../ui';
import type { BadgeIntent } from '../../ui';

export type ComponentStatus = 'ok' | 'degraded' | 'down';

export interface HealthResponse {
    status: ComponentStatus;
    version?: string;
    uptime_seconds?: number;
    pool_size?: number;
    pool_healthy?: number;
    session_active?: boolean;
    budget_today_usd?: number;
    components?: Record<string, Record<string, unknown> & { status?: ComponentStatus; detail?: string }>;
}

export interface SlosResponse {
    budget?: {
        limit?: number;
        spent?: number;
        burn_percentage?: number;
        saturated?: boolean;
    };
    upstream_error_rates?: Record<
        string,
        {
            success_count?: number;
            failure_count?: number;
            error_rate?: number;
            circuit_state?: string;
        }
    >;
}

export interface HealthApi {
    fetchHealth: () => Promise<HealthResponse | null>;
    fetchSlos?: () => Promise<SlosResponse | null>;
}

const STATUS_INTENT: Record<ComponentStatus, BadgeIntent> = {
    ok: 'success',
    degraded: 'warning',
    down: 'danger',
};

// Display order — keep critical subsystems first so a quick scan
// surfaces the right thing.
const COMPONENT_ORDER = ['session', 'store', 'cache', 'plugins', 'endpoints', 'log_queue'] as const;
const COMPONENT_LABELS: Record<string, string> = {
    session: 'Session',
    store: 'Store',
    cache: 'Cache',
    plugins: 'Plugins',
    endpoints: 'Endpoints',
    log_queue: 'Log Queue',
};

function _formatComponentDetail(name: string, comp: Record<string, unknown>): string[] {
    const out: string[] = [];
    if (comp.detail && typeof comp.detail === 'string') out.push(comp.detail);

    if (name === 'endpoints') {
        const total = comp.total as number | undefined;
        const healthy = comp.healthy as number | undefined;
        const open = comp.circuits_open as number | undefined;
        if (typeof total === 'number') out.push(`${healthy ?? 0} / ${total} healthy`);
        if (open) out.push(`${open} circuit OPEN`);
    } else if (name === 'plugins') {
        const loaded = comp.loaded as number | undefined;
        if (typeof loaded === 'number') out.push(`${loaded} loaded`);
        const ringCount = comp.ring_count as Record<string, number> | undefined;
        if (ringCount) {
            const nonEmpty = Object.entries(ringCount).filter(([, v]) => v > 0);
            if (nonEmpty.length > 0) {
                out.push(nonEmpty.map(([r, v]) => `${r}:${v}`).join(' · '));
            }
        }
    } else if (name === 'log_queue') {
        const depth = comp.depth as number | undefined;
        const max = comp.max as number | undefined;
        const sat = comp.saturation as number | undefined;
        if (typeof depth === 'number' && typeof max === 'number') {
            out.push(`${depth} / ${max}${sat !== undefined ? ` (${(sat * 100).toFixed(0)}%)` : ''}`);
        }
    } else if (name === 'cache') {
        const size = comp.size as number | undefined;
        const hits = comp.hits as number | undefined;
        if (typeof size === 'number') {
            out.push(`size ${size}${hits !== undefined ? ` · ${hits} hits` : ''}`);
        }
    }
    return out;
}

function _renderComponentTile(name: string, comp: Record<string, unknown>): HTMLElement {
    const status = (comp.status as ComponentStatus) ?? 'ok';
    const intent = STATUS_INTENT[status];
    const label = COMPONENT_LABELS[name] ?? name;

    const tile = document.createElement('article');
    tile.className = cx(
        'bg-white/[0.03] backdrop-blur-xl rounded-xl border p-3',
        intent === 'success'
            ? 'border-emerald-500/20'
            : intent === 'warning'
              ? 'border-amber-500/25'
              : 'border-rose-500/25'
    );
    tile.setAttribute('data-testid', `health-component-${name}`);

    // Header row: label left, badge right.
    const head = document.createElement('div');
    head.className = 'flex items-center justify-between mb-2';
    const labelEl = document.createElement('span');
    labelEl.className = 'text-[10px] font-bold text-slate-300 uppercase tracking-widest';
    labelEl.textContent = label;
    head.appendChild(labelEl);
    head.appendChild(
        createBadge({
            label: status,
            intent,
            size: 'sm',
            dot: status === 'ok',
            pulse: status === 'ok',
        })
    );
    tile.appendChild(head);

    // Detail lines — one per data point.
    const details = _formatComponentDetail(name, comp);
    if (details.length > 0) {
        for (const line of details) {
            const p = document.createElement('p');
            p.className = 'text-[10px] font-mono text-slate-500 leading-relaxed';
            p.textContent = line;
            tile.appendChild(p);
        }
    } else {
        const p = document.createElement('p');
        p.className = 'text-[10px] font-mono text-slate-600 italic';
        p.textContent = 'no detail';
        tile.appendChild(p);
    }
    return tile;
}

function _formatUsd(value: number | undefined): string {
    return typeof value === 'number' ? `$${value.toFixed(value < 1 ? 4 : 2)}` : '—';
}

function _renderSlos(slot: HTMLElement, data: SlosResponse | null | undefined): void {
    if (!data) {
        slot.replaceChildren();
        return;
    }

    const budget = data.budget ?? {};
    const endpoints = Object.entries(data.upstream_error_rates ?? {});
    const worst = endpoints
        .map(([id, row]) => ({ id, ...row, error_rate: row.error_rate ?? 0 }))
        .sort((a, b) => b.error_rate - a.error_rate)[0];

    const wrap = document.createElement('section');
    wrap.className = 'mt-4 border-t border-white/[0.06] pt-4';
    wrap.setAttribute('data-testid', 'health-slos');

    const head = document.createElement('div');
    head.className = 'flex items-center justify-between mb-3';
    const title = document.createElement('h3');
    title.className = 'text-[10px] font-bold text-slate-300 uppercase tracking-widest';
    title.textContent = 'SLO Snapshot';
    head.appendChild(title);
    head.appendChild(
        createBadge({
            label: budget.saturated ? 'budget saturated' : 'tracking',
            intent: budget.saturated ? 'danger' : 'success',
            size: 'sm',
            dot: !budget.saturated,
            testId: 'health-slos-badge',
        })
    );
    wrap.appendChild(head);

    const grid = document.createElement('div');
    grid.className = 'grid grid-cols-1 sm:grid-cols-2 gap-3';

    const budgetTile = document.createElement('article');
    budgetTile.className = cx(
        'bg-white/[0.03] rounded-xl border p-3',
        budget.saturated ? 'border-rose-500/25' : 'border-emerald-500/20'
    );
    budgetTile.innerHTML = `
        <p class="text-[9px] font-bold text-slate-500 uppercase tracking-widest mb-1">Budget Burn</p>
        <p class="text-lg font-black font-mono text-white">${(budget.burn_percentage ?? 0).toFixed(2)}%</p>
        <p class="text-[10px] font-mono text-slate-500">${_formatUsd(budget.spent)} spent / ${_formatUsd(budget.limit)} limit</p>
    `;
    grid.appendChild(budgetTile);

    const upstreamTile = document.createElement('article');
    upstreamTile.className = 'bg-white/[0.03] rounded-xl border border-white/[0.06] p-3';
    if (worst) {
        upstreamTile.innerHTML = `
            <p class="text-[9px] font-bold text-slate-500 uppercase tracking-widest mb-1">Worst Upstream</p>
            <p class="text-sm font-black font-mono text-white truncate">${worst.id}</p>
            <p class="text-[10px] font-mono text-slate-500">${(worst.error_rate * 100).toFixed(2)}% error · ${worst.failure_count ?? 0} fail / ${worst.success_count ?? 0} ok · ${worst.circuit_state ?? 'closed'}</p>
        `;
    } else {
        upstreamTile.innerHTML = `
            <p class="text-[9px] font-bold text-slate-500 uppercase tracking-widest mb-1">Worst Upstream</p>
            <p class="text-[10px] font-mono text-slate-600 italic">No upstream samples yet</p>
        `;
    }
    grid.appendChild(upstreamTile);

    wrap.appendChild(grid);
    slot.replaceChildren(wrap);
}

export interface HealthPanelHandle {
    refresh: () => Promise<void>;
}

export function mountHealthPanel(host: HTMLElement, api: HealthApi): HealthPanelHandle {
    const card = document.createElement('div');
    card.className = 'bg-white/[0.03] backdrop-blur-xl rounded-2xl border border-white/[0.06] p-6';
    card.setAttribute('data-testid', 'settings-health-panel');

    // Header: title + overall status badge
    const head = document.createElement('div');
    head.className = 'flex items-center justify-between mb-4';
    const title = document.createElement('h2');
    title.className = 'text-xs font-bold text-white';
    title.textContent = 'Health';
    head.appendChild(title);
    const overallSlot = document.createElement('div');
    overallSlot.setAttribute('data-testid', 'health-overall');
    head.appendChild(overallSlot);
    card.appendChild(head);

    const grid = document.createElement('div');
    grid.className = 'grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3';
    grid.setAttribute('data-testid', 'health-grid');
    card.appendChild(grid);

    const slosSlot = document.createElement('div');
    slosSlot.setAttribute('data-testid', 'health-slos-slot');
    card.appendChild(slosSlot);

    // Loading state — N skeleton tiles (one per known component).
    for (const _name of COMPONENT_ORDER) {
        grid.appendChild(createSkeleton({ shape: 'block', height: '4rem', ariaLabel: '' }));
        void _name;
    }

    host.replaceChildren(card);

    async function refresh(): Promise<void> {
        try {
            const [data, slos] = await Promise.all([
                api.fetchHealth(),
                api.fetchSlos ? api.fetchSlos().catch(() => null) : Promise.resolve(null),
            ]);
            if (!data) {
                grid.replaceChildren(
                    createErrorState({
                        title: 'No /health response',
                        description: 'Backend returned an empty body.',
                        testId: 'health-empty',
                    })
                );
                overallSlot.replaceChildren();
                slosSlot.replaceChildren();
                return;
            }

            // Overall badge in the header
            const overall = data.status ?? 'ok';
            overallSlot.replaceChildren(
                createBadge({
                    label: overall,
                    intent: STATUS_INTENT[overall],
                    size: 'sm',
                    dot: overall === 'ok',
                    pulse: overall === 'ok',
                    testId: 'health-overall-badge',
                })
            );

            // Tile per component, in canonical order. Unknown components
            // (forward-compat with new keys) render at the end.
            const components = data.components ?? {};
            const tiles: HTMLElement[] = [];
            for (const name of COMPONENT_ORDER) {
                if (components[name]) tiles.push(_renderComponentTile(name, components[name]!));
            }
            for (const [name, comp] of Object.entries(components)) {
                if (!(COMPONENT_ORDER as readonly string[]).includes(name)) {
                    tiles.push(_renderComponentTile(name, comp));
                }
            }
            if (tiles.length === 0) {
                // Older proxies that don't yet emit the components block —
                // surface a clear note rather than an empty grid.
                grid.replaceChildren(
                    createErrorState({
                        title: 'No components reported',
                        description: 'Upgrade the proxy to ≥ 1.21.9 to see per-subsystem health.',
                        testId: 'health-no-components',
                    })
                );
            } else {
                grid.replaceChildren(...tiles);
            }
            _renderSlos(slosSlot, slos);
        } catch (err) {
            grid.replaceChildren(
                createErrorState({
                    title: 'Could not load /health',
                    description: 'GET /health failed.',
                    detail: (err as Error)?.message,
                    onRetry: () => void refresh(),
                    testId: 'health-error',
                })
            );
            overallSlot.replaceChildren();
            slosSlot.replaceChildren();
        }
    }

    void refresh();
    return { refresh };
}
