import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { renderModelsKpis } from './Kpi';

let host: HTMLElement;

beforeEach(() => {
    host = document.createElement('div');
    document.body.appendChild(host);
});

afterEach(() => {
    host.remove();
});

describe('renderModelsKpis', () => {
    it('renders three tiles with provenance ℹ buttons', () => {
        renderModelsKpis(host, { total: 23, providers: 4, embedding: 5 });

        const tiles = host.querySelectorAll('article[data-testid^="kpi-"]');
        expect(tiles.length).toBe(3);

        const active = host.querySelector('[data-testid="kpi-active-models"]')!;
        expect(active.textContent).toContain('Active Models');
        expect(active.textContent).toContain('23');

        const providers = host.querySelector('[data-testid="kpi-providers"]')!;
        expect(providers.textContent).toContain('4');

        const emb = host.querySelector('[data-testid="kpi-embedding-models"]')!;
        expect(emb.textContent).toContain('5');

        // Each tile has its provenance ℹ button.
        expect(host.querySelectorAll('button[aria-label^="About "]').length).toBe(3);
    });

    it('renders skeletons when data is null and no error', () => {
        renderModelsKpis(host, null);
        // MetricTile sets aria-label="<label>: loading" on the value <p> when loading,
        // and inserts a Skeleton with aria-hidden=true inside (it's a redundant marker).
        const loadingValues = host.querySelectorAll('[aria-label$="loading"]');
        expect(loadingValues.length).toBe(3);
        // The Skeleton primitive emits aria-hidden=true when its own ariaLabel is empty.
        const decorativeSkeletons = host.querySelectorAll('span[aria-hidden="true"]');
        expect(decorativeSkeletons.length).toBeGreaterThan(0);
    });

    it('shows the em-dash + tooltip when error is set', () => {
        renderModelsKpis(host, null, '/v1/models 503');
        const values = host.querySelectorAll('p.text-red-400');
        // Three errored values, each with the upstream message in the title.
        expect(values.length).toBe(3);
        expect((values[0] as HTMLElement).title).toContain('503');
    });

    it('re-renders on subsequent calls (idempotent)', () => {
        renderModelsKpis(host, { total: 1, providers: 1, embedding: 0 });
        renderModelsKpis(host, { total: 99, providers: 7, embedding: 12 });
        const active = host.querySelector('[data-testid="kpi-active-models"]')!;
        expect(active.textContent).toContain('99');
    });
});
