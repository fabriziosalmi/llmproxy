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
});
