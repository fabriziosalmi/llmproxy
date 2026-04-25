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
        const refresh = mountDataExport(host, {
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
        });
        await refresh();
        expect(host.textContent).toContain('/var/exports');
        expect(host.querySelector('[data-testid="export-pii-badge"]')?.textContent).toContain('ON');
        expect(host.querySelector('[data-testid="export-compress-badge"]')?.textContent).toContain('OFF');
        const files = host.querySelector('[data-testid="export-files"]')!;
        expect(files.textContent).toContain('audit-2026-04-25.jsonl');
        expect(files.textContent).toContain('50.0 KB');
        expect(files.textContent).toContain('1.0 KB');
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
