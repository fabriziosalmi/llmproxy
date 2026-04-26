import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mountApiReference, __testInternals } from './ApiReference';

const _fakeSchema = {
    openapi: '3.1.0',
    info: { title: 'LLMProxy', version: '1.21.27' },
    paths: {
        '/health': {},
        '/api/v1/registry': {},
        '/api/v1/analytics/spend': {},
    },
};

let host: HTMLElement;

beforeEach(() => {
    host = document.createElement('div');
    document.body.appendChild(host);
});
afterEach(() => {
    host.remove();
});

describe('ApiReference helpers', () => {
    it('_summary pulls title + version + path count', () => {
        const s = __testInternals._summary(_fakeSchema);
        expect(s.title).toBe('LLMProxy');
        expect(s.version).toBe('3.1.0');
        expect(s.pathCount).toBe(3);
    });

    it('_summary survives missing fields', () => {
        const s = __testInternals._summary({});
        expect(s.title).toBe('LLMProxy'); // fallback default
        expect(s.version).toBe('?');
        expect(s.pathCount).toBe(0);
    });
});

describe('mountApiReference', () => {
    it('renders OpenAPI version + path count chips + a JSON snippet', async () => {
        const handle = mountApiReference(host, { fetchOpenApi: vi.fn().mockResolvedValue(_fakeSchema) });
        await handle.refresh();

        expect(host.querySelector('[data-testid="settings-api-reference"]')).not.toBeNull();
        expect(host.querySelector('[data-testid="api-reference-version"]')?.textContent).toContain('3.1.0');
        expect(host.querySelector('[data-testid="api-reference-path-count"]')?.textContent).toContain('3 paths');
        // The Snippet primitive renders the schema as JSON in a <pre><code>.
        const snip = host.querySelector('[data-testid="api-reference-snippet"]');
        expect(snip).not.toBeNull();
        const code = snip?.querySelector('pre code');
        expect(code?.textContent).toContain('"openapi": "3.1.0"');
        expect(code?.textContent).toContain('"/health"');
    });

    it('renders the Swagger Editor open-in-new-tab affordance', async () => {
        const handle = mountApiReference(host, { fetchOpenApi: vi.fn().mockResolvedValue(_fakeSchema) });
        await handle.refresh();
        expect(host.querySelector('[data-testid="api-reference-swagger-link"]')).not.toBeNull();
    });

    it('error state on fetch failure with retry hook', async () => {
        const handle = mountApiReference(host, {
            fetchOpenApi: vi.fn().mockRejectedValue(new Error('500 backend down')),
        });
        await handle.refresh();
        expect(host.querySelector('[data-testid="api-reference-error"]')).not.toBeNull();
        expect(host.textContent).toContain('500 backend down');
    });

    it('404 error specifically calls out the upgrade requirement', async () => {
        const handle = mountApiReference(host, {
            fetchOpenApi: vi.fn().mockRejectedValue(new Error('API 404: not found')),
        });
        await handle.refresh();
        expect(host.textContent).toContain('not exposed');
        expect(host.textContent).toContain('1.21.27');
    });
});
