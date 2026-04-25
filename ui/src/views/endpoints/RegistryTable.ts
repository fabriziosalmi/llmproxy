/**
 * Registry table — wraps the generic Table primitive with endpoint-specific
 * column renderers (status badge, circuit state with failure ratio,
 * priority controls, row actions).
 */
import { createBadge, createButton, createTable, cx } from '../../ui';
import type { TableColumn, TableHandle } from '../../ui';
import type { CircuitState, Endpoint } from './types';

export interface RegistryTableDeps {
    onResetCircuitBreaker: (id: string) => Promise<void>;
    onToggleEndpoint: (id: string) => Promise<void>;
    onDeleteEndpoint: (id: string) => Promise<void>;
    onUpdatePriority: (id: string, next: number) => Promise<void>;
    /** Refresh the list after a successful action. */
    refresh: () => Promise<void> | void;
    toast?: (message: string, kind?: 'success' | 'error' | 'warning' | 'info') => void;
}

const CIRCUIT_LABEL: Record<CircuitState, { label: string; intent: 'success' | 'danger' | 'warning'; pulse: boolean }> =
    {
        closed: { label: 'CLOSED', intent: 'success', pulse: false },
        open: { label: 'OPEN', intent: 'danger', pulse: true },
        half_open: { label: 'HALF', intent: 'warning', pulse: false },
    };

const STATUS_INTENT: Record<string, 'success' | 'neutral' | 'warning'> = {
    Live: 'success',
    LIVE: 'success',
    IGNORED: 'neutral',
    DEGRADED: 'warning',
};

const ICON_DOWN =
    '<svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7"/></svg>';
const ICON_UP =
    '<svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="M5 15l7-7 7 7"/></svg>';

function renderStatusCell(row: Endpoint): HTMLElement {
    const intent = STATUS_INTENT[row.status ?? ''] ?? 'warning';
    return createBadge({ label: row.status ?? '—', intent, size: 'sm', testId: `ep-status-${row.id}` });
}

function renderCircuitCell(row: Endpoint): HTMLElement {
    const stateKey = ((row.circuit_state ?? 'closed') as string).toLowerCase() as CircuitState;
    const conf = CIRCUIT_LABEL[stateKey] ?? CIRCUIT_LABEL.closed;
    const wrap = document.createElement('div');
    wrap.className = 'flex items-center gap-1.5';
    wrap.setAttribute('data-explain', `circuit:${row.id}`);
    wrap.appendChild(
        createBadge({
            label: conf.label,
            intent: conf.intent,
            size: 'sm',
            dot: conf.pulse,
            testId: `ep-circuit-${row.id}`,
        })
    );
    if ((row.failure_count ?? 0) > 0) {
        const ratio = document.createElement('span');
        ratio.className = 'text-[10px] font-mono text-slate-600';
        ratio.textContent = `${row.failure_count}/${row.failure_threshold ?? 5}`;
        wrap.appendChild(ratio);
    }
    return wrap;
}

function renderPriorityCell(row: Endpoint, deps: RegistryTableDeps): HTMLElement {
    const wrap = document.createElement('div');
    wrap.className = 'flex items-center gap-1';

    const downBtn = createButton({
        label: '',
        size: 'sm',
        variant: 'ghost',
        icon: ICON_DOWN,
        ariaLabel: `Decrease priority for ${row.id}`,
        testId: `ep-priority-down-${row.id}`,
        className: 'p-1',
    });
    downBtn.addEventListener('click', async () => {
        try {
            await deps.onUpdatePriority(row.id, Math.max(0, (row.priority ?? 0) - 1));
            await deps.refresh();
        } catch (err) {
            deps.toast?.(`Priority update failed: ${(err as Error)?.message ?? err}`, 'error');
        }
    });

    const value = document.createElement('span');
    value.className = 'text-[10px] font-mono text-slate-400 w-4 text-center';
    value.textContent = String(row.priority ?? 0);

    const upBtn = createButton({
        label: '',
        size: 'sm',
        variant: 'ghost',
        icon: ICON_UP,
        ariaLabel: `Increase priority for ${row.id}`,
        testId: `ep-priority-up-${row.id}`,
        className: 'p-1',
    });
    upBtn.addEventListener('click', async () => {
        try {
            await deps.onUpdatePriority(row.id, (row.priority ?? 0) + 1);
            await deps.refresh();
        } catch (err) {
            deps.toast?.(`Priority update failed: ${(err as Error)?.message ?? err}`, 'error');
        }
    });

    wrap.appendChild(downBtn);
    wrap.appendChild(value);
    wrap.appendChild(upBtn);
    return wrap;
}

function renderActionsCell(row: Endpoint, deps: RegistryTableDeps): HTMLElement {
    const wrap = document.createElement('div');
    wrap.className = 'flex items-center justify-end gap-1';

    const inspect = createButton({
        label: 'Inspect',
        size: 'sm',
        variant: 'ghost',
        testId: `ep-inspect-${row.id}`,
    });
    inspect.dataset.drilldown = `endpoint:${row.id}`;
    wrap.appendChild(inspect);

    const resetCb = createButton({
        label: 'Reset CB',
        size: 'sm',
        variant: 'ghost',
        testId: `ep-reset-cb-${row.id}`,
    });
    resetCb.addEventListener('click', async () => {
        try {
            await deps.onResetCircuitBreaker(row.id);
            deps.toast?.(`Circuit breaker ${row.id} reset to CLOSED`, 'success');
            await deps.refresh();
        } catch (err) {
            deps.toast?.(`Reset failed: ${(err as Error)?.message ?? err}`, 'error');
        }
    });
    wrap.appendChild(resetCb);

    const toggle = createButton({
        label: 'Toggle',
        size: 'sm',
        variant: 'ghost',
        testId: `ep-toggle-${row.id}`,
    });
    toggle.addEventListener('click', async () => {
        try {
            await deps.onToggleEndpoint(row.id);
            deps.toast?.(`Endpoint ${row.id} toggled`, 'success');
            await deps.refresh();
        } catch (err) {
            deps.toast?.(`Toggle failed: ${(err as Error)?.message ?? err}`, 'error');
        }
    });
    wrap.appendChild(toggle);

    const del = createButton({
        label: 'Delete',
        size: 'sm',
        variant: 'ghost',
        testId: `ep-delete-${row.id}`,
        className: 'hover:text-rose-400',
    });
    del.addEventListener('click', async () => {
        const { confirm } = await import('../../ui');
        const ok = await confirm({
            title: 'Delete endpoint',
            message: `Remove "${row.id}" from the registry? Active traffic will be re-routed via the fallback chain.`,
            confirmLabel: 'Delete',
            danger: true,
        });
        if (!ok) return;
        try {
            await deps.onDeleteEndpoint(row.id);
            deps.toast?.(`Endpoint ${row.id} deleted`, 'success');
            await deps.refresh();
        } catch (err) {
            deps.toast?.(`Delete failed: ${(err as Error)?.message ?? err}`, 'error');
        }
    });
    wrap.appendChild(del);

    return wrap;
}

export function createRegistryTable(initial: Endpoint[], deps: RegistryTableDeps): TableHandle<Endpoint> {
    const columns: TableColumn<Endpoint>[] = [
        {
            key: 'id',
            label: 'Endpoint',
            sortable: true,
            sortValue: (r) => r.name ?? r.id,
            render: (row) => {
                const wrap = document.createElement('div');
                const name = document.createElement('p');
                name.className = 'text-[11px] font-bold text-white';
                name.textContent = row.name ?? row.id;
                const url = document.createElement('p');
                url.className = 'text-[9px] text-slate-500 font-mono truncate max-w-xs';
                url.textContent = row.url;
                wrap.appendChild(name);
                wrap.appendChild(url);
                return wrap;
            },
        },
        { key: 'status', label: 'Status', sortable: true, render: renderStatusCell },
        {
            key: 'circuit_state',
            label: 'Circuit',
            sortable: true,
            sortValue: (r) => String(r.circuit_state ?? 'closed').toLowerCase(),
            render: renderCircuitCell,
        },
        {
            key: 'latency',
            label: 'Latency',
            sortable: true,
            sortValue: (r) =>
                typeof r.latency === 'number' ? r.latency : Number.parseFloat(String(r.latency ?? '0')) || 0,
            render: (row) => {
                const span = document.createElement('span');
                span.className = 'text-[10px] font-mono text-slate-400';
                span.textContent = row.latency === undefined || row.latency === null ? '—' : String(row.latency);
                return span;
            },
        },
        { key: 'priority', label: 'Priority', sortable: true, render: (row) => renderPriorityCell(row, deps) },
        { key: '__actions', label: 'Actions', align: 'right', render: (row) => renderActionsCell(row, deps) },
    ];

    return createTable<Endpoint>({
        columns,
        rows: initial,
        rowKey: (r) => r.id,
        initialSort: { key: 'priority', direction: 'desc' },
        className: cx('mb-4'),
        testId: 'registry-table',
    });
}
