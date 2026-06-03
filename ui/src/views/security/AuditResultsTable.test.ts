import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderAuditTable } from './AuditResultsTable';

let host: HTMLElement;

beforeEach(() => {
    host = document.createElement('div');
    document.body.appendChild(host);
});

afterEach(() => {
    host.remove();
    vi.restoreAllMocks();
});

describe('renderAuditTable', () => {
    it('renders CSV and JSON export buttons for the current result set', () => {
        const createObjectURL = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:test');
        const revokeObjectURL = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});
        const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});

        renderAuditTable(
            host,
            [
                {
                    req_id: 'req-1',
                    ts: 1_700_000_000,
                    model: 'gpt-4o-mini',
                    status: 200,
                    prompt_tokens: 10,
                    completion_tokens: 4,
                    cost_usd: 0.001,
                    blocked: false,
                },
            ],
            'Last 24h'
        );

        host.querySelector<HTMLButtonElement>('[data-testid="audit-export-csv"]')!.click();
        host.querySelector<HTMLButtonElement>('[data-testid="audit-export-json"]')!.click();

        expect(host.textContent).toContain('Export CSV');
        expect(host.textContent).toContain('Export JSON');
        expect(createObjectURL).toHaveBeenCalledTimes(2);
        expect(click).toHaveBeenCalledTimes(2);
        expect(revokeObjectURL).toHaveBeenCalledWith('blob:test');
    });
});
