/**
 * Models table — wraps the Table primitive with a provider-color cell and
 * an inline EMB badge for embedding rows. Each row carries a
 * `data-drilldown="model:<id>"` attribute on the Inspect button so the
 * existing drilldown service can hook in.
 */
import { createBadge, createButton, createTable } from '../../ui';
import { copyText } from '../../../services/file_actions.js';
import type { TableColumn, TableHandle } from '../../ui';
import { isEmbeddingModel, providerColor, type Model } from './types';

export interface ModelsTableDeps {
    /** Optional toast helper, only used if you wire row callbacks later. */
    toast?: (message: string, kind?: 'success' | 'error' | 'warning' | 'info') => void;
}

function renderIdCell(row: Model): HTMLElement {
    const wrap = document.createElement('div');
    wrap.className = 'flex items-center gap-2';

    const id = document.createElement('span');
    id.className = 'text-[11px] font-bold text-white font-mono';
    id.textContent = row.id;
    wrap.appendChild(id);

    if (isEmbeddingModel(row.id)) {
        wrap.appendChild(createBadge({ label: 'EMB', intent: 'primary', size: 'sm', testId: `model-emb-${row.id}` }));
    }
    return wrap;
}

function renderProviderCell(row: Model): HTMLElement {
    const span = document.createElement('span');
    span.className = `text-[10px] font-semibold uppercase ${providerColor(row.owned_by)}`;
    span.textContent = row.owned_by;
    span.setAttribute('data-testid', `model-provider-${row.id}`);
    return span;
}

function renderInspectCell(row: Model, deps: ModelsTableDeps): HTMLElement {
    const wrap = document.createElement('div');
    wrap.className = 'flex justify-end gap-1';
    const copy = createButton({
        label: 'Copy ID',
        size: 'sm',
        variant: 'ghost',
        testId: `model-copy-${row.id}`,
    });
    copy.addEventListener('click', async (ev) => {
        ev.stopPropagation();
        const ok = await copyText(row.id);
        deps.toast?.(ok ? `Copied ${row.id}` : `Copy failed for ${row.id}`, ok ? 'success' : 'error');
    });
    wrap.appendChild(copy);

    const btn = createButton({
        label: 'Inspect',
        size: 'sm',
        variant: 'ghost',
        testId: `model-inspect-${row.id}`,
    });
    btn.dataset.drilldown = `model:${row.id}`;
    wrap.appendChild(btn);
    return wrap;
}

export function createModelsTable(rows: Model[], deps: ModelsTableDeps = {}): TableHandle<Model> {
    void deps;
    const columns: TableColumn<Model>[] = [
        {
            key: 'id',
            label: 'Model ID',
            sortable: true,
            sortValue: (r) => r.id.toLowerCase(),
            render: renderIdCell,
        },
        {
            key: 'owned_by',
            label: 'Provider',
            sortable: true,
            sortValue: (r) => r.owned_by.toLowerCase(),
            render: renderProviderCell,
        },
        {
            key: '__inspect',
            label: 'Actions',
            align: 'right',
            render: (row) => renderInspectCell(row, deps),
        },
    ];

    const empty = document.createElement('p');
    empty.className = 'text-[11px] text-slate-500 font-mono';
    empty.textContent = 'No models match this filter.';

    return createTable<Model>({
        columns,
        rows,
        rowKey: (r) => r.id,
        initialSort: { key: 'id', direction: 'asc' },
        emptyState: empty,
        testId: 'models-table',
    });
}
