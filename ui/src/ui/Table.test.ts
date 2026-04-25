import { describe, expect, it, vi } from 'vitest';
import { createTable, type TableColumn } from './Table';

interface Endpoint {
    id: string;
    requests: number;
    healthy: boolean;
}

const COLUMNS: TableColumn<Endpoint>[] = [
    { key: 'id', label: 'Endpoint', sortable: true },
    { key: 'requests', label: 'Requests', align: 'right', sortable: true },
    {
        key: 'healthy',
        label: 'Health',
        render: (row) => (row.healthy ? 'OK' : 'DEAD'),
    },
];

const ROWS: Endpoint[] = [
    { id: 'openai', requests: 100, healthy: true },
    { id: 'anthropic', requests: 250, healthy: true },
    { id: 'mistral', requests: 12, healthy: false },
];

describe('createTable', () => {
    it('renders headers, rows and uses custom renderers', () => {
        const t = createTable({ columns: COLUMNS, rows: ROWS });
        const headers = Array.from(t.root.querySelectorAll('th')).map((th) =>
            th.textContent?.replace(/\s+/g, ' ').trim()
        );
        expect(headers).toEqual(['Endpoint', 'Requests', 'Health']);
        const cells = Array.from(t.root.querySelectorAll('tbody tr')).map((tr) =>
            Array.from(tr.querySelectorAll('td')).map((td) => td.textContent)
        );
        expect(cells).toHaveLength(3);
        expect(cells[0]).toEqual(['openai', '100', 'OK']);
        expect(cells[2]).toEqual(['mistral', '12', 'DEAD']);
    });

    it('shows the empty state when rows is empty', () => {
        const empty = document.createElement('p');
        empty.textContent = 'No endpoints yet.';
        const t = createTable({ columns: COLUMNS, rows: [], emptyState: empty });
        expect(t.root.querySelector('tbody td')?.textContent).toContain('No endpoints yet');
    });

    it('clicking a sortable header sorts ascending, clicking again flips to descending', () => {
        const t = createTable({ columns: COLUMNS, rows: ROWS });
        const reqHeader = Array.from(t.root.querySelectorAll('th')).find((th) => th.dataset.key === 'requests')!;
        reqHeader.click();
        let cells = Array.from(t.root.querySelectorAll('tbody td:nth-child(2)')).map((td) => td.textContent);
        expect(cells).toEqual(['12', '100', '250']);
        expect(reqHeader.getAttribute('aria-sort')).toBe('ascending');

        reqHeader.click();
        cells = Array.from(t.root.querySelectorAll('tbody td:nth-child(2)')).map((td) => td.textContent);
        expect(cells).toEqual(['250', '100', '12']);
        expect(reqHeader.getAttribute('aria-sort')).toBe('descending');
    });

    it('non-sortable header does not get a click handler or aria-sort', () => {
        const t = createTable({ columns: COLUMNS, rows: ROWS });
        const healthHeader = Array.from(t.root.querySelectorAll('th')).find((th) => th.dataset.key === 'healthy')!;
        healthHeader.click();
        expect(healthHeader.getAttribute('aria-sort')).toBeNull();
    });

    it('row click fires onRowClick with the row data; Enter/Space activates from keyboard', () => {
        const onRowClick = vi.fn();
        const t = createTable({ columns: COLUMNS, rows: ROWS, onRowClick });
        const tr = t.root.querySelector('tbody tr')!;
        (tr as HTMLElement).click();
        expect(onRowClick).toHaveBeenCalledWith(ROWS[0]);

        tr.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
        tr.dispatchEvent(new KeyboardEvent('keydown', { key: ' ' }));
        expect(onRowClick).toHaveBeenCalledTimes(3);
    });

    it('setRows replaces the body in place, leaving the headers untouched', () => {
        const t = createTable({ columns: COLUMNS, rows: ROWS });
        const headBefore = t.root.querySelector('thead')!;
        t.setRows([{ id: 'groq', requests: 9, healthy: true }]);
        expect(t.root.querySelector('thead')).toBe(headBefore);
        const cells = Array.from(t.root.querySelectorAll('tbody td')).map((td) => td.textContent);
        expect(cells).toEqual(['groq', '9', 'OK']);
    });

    it('setSort programmatically applies a sort', () => {
        const t = createTable({ columns: COLUMNS, rows: ROWS });
        t.setSort('id', 'desc');
        const cells = Array.from(t.root.querySelectorAll('tbody td:first-child')).map((td) => td.textContent);
        expect(cells).toEqual(['openai', 'mistral', 'anthropic']);
    });

    it('rowKey is forwarded onto data-key', () => {
        const t = createTable({ columns: COLUMNS, rows: ROWS, rowKey: (r) => r.id });
        const keys = Array.from(t.root.querySelectorAll('tbody tr')).map((tr) => (tr as HTMLElement).dataset.key);
        expect(keys).toEqual(['openai', 'anthropic', 'mistral']);
    });

    // L.2 — hideBelow stamps Tailwind responsive classes on header AND every cell
    // so the column collapses cleanly on phones (no half-rendered rows).

    it('hideBelow="sm" applies hidden + sm:table-cell to header and every body cell', () => {
        const cols: TableColumn<Endpoint>[] = [
            { key: 'id', label: 'Endpoint' },
            { key: 'requests', label: 'Requests', hideBelow: 'sm' },
        ];
        const t = createTable({ columns: cols, rows: ROWS });

        const ths = t.root.querySelectorAll<HTMLTableCellElement>('thead th');
        expect(ths[0]?.className).not.toContain('hidden');
        expect(ths[1]?.className).toContain('hidden');
        expect(ths[1]?.className).toContain('sm:table-cell');

        const tds = t.root.querySelectorAll<HTMLTableCellElement>('tbody tr td:nth-child(2)');
        expect(tds.length).toBe(ROWS.length);
        tds.forEach((td) => {
            expect(td.className).toContain('hidden');
            expect(td.className).toContain('sm:table-cell');
        });
    });

    it('hideBelow="md" uses md: breakpoint instead of sm:', () => {
        const cols: TableColumn<Endpoint>[] = [
            { key: 'id', label: 'Endpoint' },
            { key: 'requests', label: 'Requests', hideBelow: 'md' },
        ];
        const t = createTable({ columns: cols, rows: ROWS });
        const th = t.root.querySelectorAll<HTMLTableCellElement>('thead th')[1];
        expect(th?.className).toContain('md:table-cell');
        expect(th?.className).not.toContain('sm:');
    });
});
