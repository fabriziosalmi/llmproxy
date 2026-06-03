import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mountDataExport } from './DataExport';

let host: HTMLElement;

beforeEach(() => {
    host = document.createElement('div');
    document.body.appendChild(host);
});

afterEach(() => {
    host.remove();
});

describe('mountDataExport', () => {
    it('renders output_dir, options, and recent files when enabled', async () => {
        const writeText = vi.fn().mockResolvedValue(undefined);
        Object.defineProperty(navigator, 'clipboard', {
            configurable: true,
            value: { writeText },
        });
        const toast = vi.fn();
        const refresh = mountDataExport(
            host,
            {
                fetchExportStatus: vi.fn().mockResolvedValue({
                    enabled: true,
                    output_dir: '/var/exports',
                    scrub_pii: true,
                    compress: false,
                    files: [
                        { name: 'audit-2026-04-25.jsonl', size_bytes: 51200 },
                        { name: 'audit-2026-04-24.jsonl', size_bytes: 1024 },
                    ],
                }),
                exportFileUrl: (name) => `/api/v1/export/files/${name}`,
            },
            { toast }
        );
        await refresh();
        expect(host.textContent).toContain('/var/exports');
        expect(host.querySelector('[data-testid="export-pii-badge"]')?.textContent).toContain('ON');
        expect(host.querySelector('[data-testid="export-compress-badge"]')?.textContent).toContain('OFF');
        const files = host.querySelector('[data-testid="export-files"]')!;
        expect(files.textContent).toContain('audit-2026-04-25.jsonl');
        expect(files.textContent).toContain('50.0 KB');
        expect(files.textContent).toContain('1.0 KB');
        expect(
            host.querySelector<HTMLAnchorElement>('[data-testid="export-download-audit-2026-04-25.jsonl"]')?.href
        ).toContain('/api/v1/export/files/audit-2026-04-25.jsonl');

        host.querySelector<HTMLButtonElement>('[data-testid="export-copy-audit-2026-04-25.jsonl"]')!.click();
        await new Promise((r) => setTimeout(r, 0));
        expect(writeText).toHaveBeenCalledWith('/var/exports/audit-2026-04-25.jsonl');
        expect(toast).toHaveBeenCalledWith(expect.stringContaining('audit-2026-04-25.jsonl'), 'success');
    });

    it('renders an empty-state when export is disabled in config.yaml', async () => {
        const refresh = mountDataExport(host, {
            fetchExportStatus: vi.fn().mockResolvedValue({ enabled: false }),
        });
        await refresh();
        expect(host.querySelector('[data-testid="export-disabled"]')).not.toBeNull();
    });

    it('renders a "no files yet" line when files is empty but export is enabled', async () => {
        const refresh = mountDataExport(host, {
            fetchExportStatus: vi.fn().mockResolvedValue({
                enabled: true,
                output_dir: '/tmp',
                scrub_pii: false,
                compress: true,
                files: [],
            }),
        });
        await refresh();
        expect(host.textContent).toContain('No export files yet');
    });

    it('shows ErrorState with retry when /export/status 503s', async () => {
        const refresh = mountDataExport(host, {
            fetchExportStatus: vi.fn().mockRejectedValue(new Error('500')),
        });
        await refresh();
        expect(host.querySelector('[data-testid="export-error"]')).not.toBeNull();
    });
});
