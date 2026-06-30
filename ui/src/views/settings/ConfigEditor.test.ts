import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mountConfigEditor } from './ConfigEditor';

let host: HTMLElement;

beforeEach(() => {
    host = document.createElement('div');
    document.body.appendChild(host);
});

afterEach(() => {
    host.remove();
    vi.restoreAllMocks();
});

const flush = () => new Promise((r) => setTimeout(r, 0));

function baseApi(over: Partial<Parameters<typeof mountConfigEditor>[1]> = {}) {
    return {
        fetchConfigRaw: vi.fn().mockResolvedValue({ yaml: 'server:\n  port: 8090\n' }),
        validateConfig: vi.fn().mockResolvedValue({ valid: true, errors: [], warnings: [] }),
        applyConfig: vi.fn().mockResolvedValue({ applied: true, warnings: [], backup: 'config.yaml.bak.1' }),
        ...over,
    };
}

describe('mountConfigEditor', () => {
    it('loads the raw config source into the textarea', async () => {
        const handle = mountConfigEditor(host, baseApi());
        await handle.refresh();
        const ta = host.querySelector<HTMLTextAreaElement>('[data-testid="config-editor-textarea"]');
        expect(ta).not.toBeNull();
        expect(ta?.value).toContain('port: 8090');
    });

    it('surfaces validation errors from a dry-run', async () => {
        const api = baseApi({
            validateConfig: vi.fn().mockResolvedValue({
                valid: false,
                errors: ['Proxy authentication requires LLM_PROXY_API_KEYS'],
                warnings: [],
            }),
        });
        const handle = mountConfigEditor(host, api);
        await handle.refresh();
        host.querySelector<HTMLButtonElement>('[data-testid="config-validate-btn"]')!.click();
        await flush();
        expect(api.validateConfig).toHaveBeenCalled();
        expect(host.textContent).toContain('LLM_PROXY_API_KEYS');
    });

    it('confirms valid config', async () => {
        const handle = mountConfigEditor(host, baseApi());
        await handle.refresh();
        host.querySelector<HTMLButtonElement>('[data-testid="config-validate-btn"]')!.click();
        await flush();
        expect(host.textContent).toContain('valid');
    });

    it('does NOT apply when pre-validation fails', async () => {
        const api = baseApi({
            validateConfig: vi.fn().mockResolvedValue({ valid: false, errors: ['bad'], warnings: [] }),
        });
        const handle = mountConfigEditor(host, api);
        await handle.refresh();
        host.querySelector<HTMLButtonElement>('[data-testid="config-apply-btn"]')!.click();
        await flush();
        expect(api.applyConfig).not.toHaveBeenCalled();
        expect(host.textContent).toContain('bad');
    });
});
