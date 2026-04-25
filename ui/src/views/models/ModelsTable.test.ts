import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { createModelsTable } from './ModelsTable';
import type { Model } from './types';

const ROWS: Model[] = [
    { id: 'gpt-4o-mini', owned_by: 'openai' },
    { id: 'claude-sonnet-4', owned_by: 'anthropic' },
    { id: 'text-embedding-3-small', owned_by: 'openai' },
    { id: 'mistral-embed', owned_by: 'mistral' },
];

let host: HTMLElement;

beforeEach(() => {
    host = document.createElement('div');
    document.body.appendChild(host);
});

afterEach(() => {
    host.remove();
});

describe('createModelsTable', () => {
    it('renders one row per model with id and provider', () => {
        const t = createModelsTable(ROWS);
        host.appendChild(t.root);
        const rows = host.querySelectorAll('tbody tr');
        expect(rows.length).toBe(ROWS.length);
        expect(host.textContent).toContain('gpt-4o-mini');
        expect(host.textContent).toContain('claude-sonnet-4');
    });

    it('marks embedding rows with an EMB badge', () => {
        const t = createModelsTable(ROWS);
        host.appendChild(t.root);
        // Two embedding rows in the seed.
        expect(host.querySelector('[data-testid="model-emb-text-embedding-3-small"]')).not.toBeNull();
        expect(host.querySelector('[data-testid="model-emb-mistral-embed"]')).not.toBeNull();
        // Chat models do not.
        expect(host.querySelector('[data-testid="model-emb-gpt-4o-mini"]')).toBeNull();
    });

    it('Inspect button forwards data-drilldown for the existing drilldown service', () => {
        const t = createModelsTable(ROWS);
        host.appendChild(t.root);
        const inspect = host.querySelector<HTMLElement>('[data-testid="model-inspect-gpt-4o-mini"]');
        expect(inspect).not.toBeNull();
        expect(inspect?.dataset.drilldown).toBe('model:gpt-4o-mini');
    });

    it('provider cell carries the bespoke color class for known providers', () => {
        const t = createModelsTable(ROWS);
        host.appendChild(t.root);
        const openaiCell = host.querySelector<HTMLElement>('[data-testid="model-provider-gpt-4o-mini"]');
        expect(openaiCell?.className).toContain('emerald');
        const anthropicCell = host.querySelector<HTMLElement>('[data-testid="model-provider-claude-sonnet-4"]');
        expect(anthropicCell?.className).toContain('amber');
    });

    it('shows the empty-state slot when rows is empty', () => {
        const t = createModelsTable([]);
        host.appendChild(t.root);
        expect(host.textContent).toContain('No models match this filter');
    });

    it('clicking a sortable header toggles the sort indicator', () => {
        const t = createModelsTable(ROWS);
        host.appendChild(t.root);
        const idHeader = Array.from(host.querySelectorAll('th')).find((th) => th.dataset.key === 'id')!;
        // initial sort is asc on id, so the table starts sorted ascending.
        expect(idHeader.getAttribute('aria-sort')).toBe('ascending');
        idHeader.click();
        expect(idHeader.getAttribute('aria-sort')).toBe('descending');
    });
});
