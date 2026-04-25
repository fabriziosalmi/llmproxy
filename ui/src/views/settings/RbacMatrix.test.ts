import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mountRbacMatrix } from './RbacMatrix';

let host: HTMLElement;

beforeEach(() => {
    host = document.createElement('div');
    document.body.appendChild(host);
});

afterEach(() => {
    host.remove();
});

describe('mountRbacMatrix', () => {
    it('renders a permission × role table with check / dash cells', async () => {
        const refresh = mountRbacMatrix(host, {
            fetchRbacRoles: vi.fn().mockResolvedValue({
                admin: ['read:audit', 'write:keys', 'delete:endpoint'],
                auditor: ['read:audit'],
            }),
        });
        await refresh();
        const table = host.querySelector('[data-testid="rbac-matrix-table"]')!;
        expect(table).not.toBeNull();
        const headers = Array.from(table.querySelectorAll('thead th')).map((th) => th.textContent);
        expect(headers).toContain('admin');
        expect(headers).toContain('auditor');
        // Permissions appear as row labels.
        expect(host.textContent).toContain('read:audit');
        expect(host.textContent).toContain('delete:endpoint');
        // The auditor row has check on read:audit, dash elsewhere.
        const auditorRow = Array.from(table.querySelectorAll('tbody tr')).find((tr) =>
            tr.textContent?.startsWith('read:audit')
        )!;
        expect(auditorRow.textContent).toContain('✓');
    });

    it('renders an empty-state when no roles are configured', async () => {
        const refresh = mountRbacMatrix(host, {
            fetchRbacRoles: vi.fn().mockResolvedValue({}),
        });
        await refresh();
        expect(host.querySelector('[data-testid="rbac-empty"]')).not.toBeNull();
    });

    it('shows ErrorState with retry when /rbac/roles 503s', async () => {
        const refresh = mountRbacMatrix(host, {
            fetchRbacRoles: vi.fn().mockRejectedValue(new Error('connection refused')),
        });
        await refresh();
        const err = host.querySelector('[data-testid="rbac-error"]');
        expect(err).not.toBeNull();
        expect(err?.querySelector('[data-testid="error-state-retry"]')).not.toBeNull();
    });
});
