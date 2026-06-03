import { describe, expect, it, vi } from 'vitest';
import { renderSecuritySummary } from './index';

describe('renderSecuritySummary', () => {
    it('loads semantic corpus stats from the backend API', async () => {
        const trackedIps = document.createElement('div');
        const signingStatus = document.createElement('div');
        const retentionInfo = document.createElement('div');
        const corpusPatterns = document.createElement('div');
        const corpusCategories = document.createElement('div');

        const originalFetch = globalThis.fetch;
        globalThis.fetch = vi.fn().mockResolvedValue({
            ok: true,
            json: () => Promise.resolve({ retention_days: 30, legal_basis: 'legitimate_interest' }),
        }) as unknown as typeof fetch;

        await renderSecuritySummary(
            {
                fetchGuardsStatus: vi.fn().mockResolvedValue({
                    security_shield: { threat_ledger: { tracked_ips: 4 } },
                    response_signing: { enabled: true },
                }),
                fetchSecurityCorpus: vi.fn().mockResolvedValue({
                    total_patterns: 123,
                    categories: { override: 70, extraction: 53 },
                }),
                getToken: () => 'sk-test',
                origin: 'http://localhost',
                toast: vi.fn(),
                timerange: {
                    sinceEpochMs: () => null,
                    untilEpochMs: () => null,
                    label: () => 'All time',
                },
            },
            { trackedIps, signingStatus, retentionInfo, corpusPatterns, corpusCategories }
        );

        expect(corpusPatterns.textContent).toBe('123');
        expect(corpusCategories.textContent).toContain('override');
        expect(corpusCategories.textContent).toContain('53');

        globalThis.fetch = originalFetch;
    });
});
