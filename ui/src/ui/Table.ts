/**
 * Table primitive — typed, sortable, with built-in empty state.
 *
 * Generic over the row shape. Each column declares its key, label,
 * alignment, optional width, sortability, and a custom renderer. The
 * default renderer is `String(row[key])`.
 *
 * `setRows()` re-renders the body in place — no full DOM replacement of
 * the wrapper, so external scrolling state and any sticky header survive.
 */
import { cx } from './classnames';

export type CellAlign = 'left' | 'right' | 'center';
export type SortDirection = 'asc' | 'desc';

export interface TableColumn<T> {
    key: string;
    label: string;
    align?: CellAlign;
    /** CSS width for the column (e.g. '8rem'). */
    width?: string;
    sortable?: boolean;
    /**
     * Hide this column below the given Tailwind breakpoint. Maps to
     *   sm → `hidden sm:table-cell` (visible from 640px up)
     *   md → `hidden md:table-cell` (visible from 768px up)
     * The header AND every cell get the class so colspan stays honest.
     * Use sparingly — operators on phones still need the data, just not
     * every column at once.
     */
    hideBelow?: 'sm' | 'md';
    /** Returns a string or DOM node for the cell. Falls back to String(row[key]). */
    render?: (row: T) => string | HTMLElement;
    /** Pulls a comparable value out of the row when sorting on this column. */
    sortValue?: (row: T) => string | number;
}

export interface TableOptions<T> {
    columns: TableColumn<T>[];
    rows: T[];
    /** Stable key per row — defaults to row index. */
    rowKey?: (row: T, index: number) => string | number;
    onRowClick?: (row: T) => void;
    initialSort?: { key: string; direction: SortDirection };
    /** Element shown when rows is empty (or filtered to nothing). */
    emptyState?: HTMLElement;
    className?: string;
    testId?: string;
}

export interface TableHandle<T> {
    root: HTMLElement;
    setRows(rows: T[]): void;
    setSort(key: string | null, direction?: SortDirection): void;
}

const ALIGN_CLASS: Record<CellAlign, string> = {
    left: 'text-left',
    right: 'text-right',
    center: 'text-center',
};

const HIDE_BELOW_CLASS: Record<NonNullable<TableColumn<unknown>['hideBelow']>, string> = {
    sm: 'hidden sm:table-cell',
    md: 'hidden md:table-cell',
};

export function createTable<T>(opts: TableOptions<T>): TableHandle<T> {
    let rows = opts.rows;
    let sort: { key: string; direction: SortDirection } | null = opts.initialSort ?? null;

    const root = document.createElement('div');
    root.className = cx('overflow-x-auto rounded-xl border border-white/[0.06]', opts.className);
    if (opts.testId) root.setAttribute('data-testid', opts.testId);

    const table = document.createElement('table');
    table.className = 'w-full text-[11px] font-mono';
    table.setAttribute('role', 'table');

    const thead = document.createElement('thead');
    thead.className = 'sticky top-0 bg-[#0a0a0c] border-b border-white/[0.06]';
    const headRow = document.createElement('tr');
    headRow.setAttribute('role', 'row');

    for (const col of opts.columns) {
        const th = document.createElement('th');
        th.scope = 'col';
        th.setAttribute('role', 'columnheader');
        th.className = cx(
            'px-3 py-2 text-[9px] font-bold uppercase tracking-widest text-slate-500 select-none',
            ALIGN_CLASS[col.align ?? 'left'],
            col.sortable && 'cursor-pointer hover:text-slate-200',
            col.hideBelow && HIDE_BELOW_CLASS[col.hideBelow]
        );
        if (col.width) th.style.width = col.width;
        th.dataset.key = col.key;

        const labelSpan = document.createElement('span');
        labelSpan.textContent = col.label;
        th.appendChild(labelSpan);

        if (col.sortable) {
            const indicator = document.createElement('span');
            indicator.className = 'ml-1 inline-block text-slate-600';
            indicator.dataset.role = 'sort-indicator';
            indicator.textContent = '';
            th.appendChild(indicator);
            th.addEventListener('click', () => {
                if (!sort || sort.key !== col.key) {
                    sort = { key: col.key, direction: 'asc' };
                } else {
                    sort = { key: col.key, direction: sort.direction === 'asc' ? 'desc' : 'asc' };
                }
                paintHead();
                paintBody();
            });
        }
        headRow.appendChild(th);
    }
    thead.appendChild(headRow);
    table.appendChild(thead);

    const tbody = document.createElement('tbody');
    table.appendChild(tbody);
    root.appendChild(table);

    const paintHead = (): void => {
        const ths = headRow.querySelectorAll('th');
        ths.forEach((th) => {
            const indicator = th.querySelector<HTMLElement>('[data-role="sort-indicator"]');
            if (!indicator) return;
            if (sort && sort.key === th.dataset.key) {
                indicator.textContent = sort.direction === 'asc' ? '▲' : '▼';
                indicator.classList.remove('text-slate-600');
                indicator.classList.add('text-slate-200');
                th.setAttribute('aria-sort', sort.direction === 'asc' ? 'ascending' : 'descending');
            } else {
                indicator.textContent = '';
                indicator.classList.remove('text-slate-200');
                indicator.classList.add('text-slate-600');
                th.removeAttribute('aria-sort');
            }
        });
    };

    const sortedRows = (): T[] => {
        if (!sort) return rows;
        const col = opts.columns.find((c) => c.key === sort!.key);
        if (!col) return rows;
        const getter = col.sortValue ?? ((row: T) => (row as Record<string, unknown>)[col.key] as string | number);
        const dir = sort.direction === 'asc' ? 1 : -1;
        return [...rows].sort((a, b) => {
            const av = getter(a);
            const bv = getter(b);
            if (av === bv) return 0;
            if (av === undefined || av === null) return 1;
            if (bv === undefined || bv === null) return -1;
            return av < bv ? -1 * dir : 1 * dir;
        });
    };

    const paintBody = (): void => {
        tbody.replaceChildren();
        if (rows.length === 0) {
            const tr = document.createElement('tr');
            const td = document.createElement('td');
            td.colSpan = opts.columns.length;
            td.className = 'p-4';
            if (opts.emptyState) td.appendChild(opts.emptyState);
            else td.textContent = 'No data.';
            tr.appendChild(td);
            tbody.appendChild(tr);
            return;
        }

        const visible = sortedRows();
        for (let i = 0; i < visible.length; i++) {
            const row = visible[i]!;
            const tr = document.createElement('tr');
            tr.setAttribute('role', 'row');
            tr.className = cx(
                'border-t border-white/[0.04] transition-colors',
                opts.onRowClick && 'cursor-pointer hover:bg-white/[0.03]'
            );
            const key = opts.rowKey ? String(opts.rowKey(row, i)) : String(i);
            tr.dataset.key = key;
            if (opts.onRowClick) {
                tr.tabIndex = 0;
                tr.addEventListener('click', () => opts.onRowClick?.(row));
                tr.addEventListener('keydown', (e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        opts.onRowClick?.(row);
                    }
                });
            }

            for (const col of opts.columns) {
                const td = document.createElement('td');
                td.className = cx(
                    'px-3 py-2 text-slate-300 align-middle',
                    ALIGN_CLASS[col.align ?? 'left'],
                    col.hideBelow && HIDE_BELOW_CLASS[col.hideBelow]
                );
                if (col.render) {
                    const out = col.render(row);
                    if (typeof out === 'string') td.innerHTML = out;
                    else td.appendChild(out);
                } else {
                    const val = (row as Record<string, unknown>)[col.key];
                    td.textContent = val === undefined || val === null ? '' : String(val);
                }
                tr.appendChild(td);
            }
            tbody.appendChild(tr);
        }
    };

    paintHead();
    paintBody();

    return {
        root,
        setRows(next: T[]): void {
            rows = next;
            paintBody();
        },
        setSort(key: string | null, direction: SortDirection = 'asc'): void {
            sort = key ? { key, direction } : null;
            paintHead();
            paintBody();
        },
    };
}
