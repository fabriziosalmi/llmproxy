import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mountConfigYaml } from './ConfigYaml';

let host: HTMLElement;

beforeEach(() => {
    host = document.createElement('div');
    document.body.appendChild(host);
});

afterEach(() => {
    host.remove();
});

describe('mountConfigYaml', () => {
    it('renders the YAML inside a Snippet primitive on success', async () => {
        const yaml = "endpoints:\n  openai:\n    api_key: <REDACTED>\n";
        const refresh = mountConfigYaml(host, {
            fetchConfigYaml: vi.fn().mockResolvedValue({ yaml }),
        });
        await refresh();

        expect(host.querySelector('[data-testid="settings-config-yaml"]')).not.toBeNull();
        const snippet = host.querySelector('[data-testid="config-yaml-snippet"]');
        expect(snippet).not.toBeNull();
        expect(snippet?.textContent).toContain('endpoints:');
        expect(snippet?.textContent).toContain('<REDACTED>');
    });

    it('shows the error state with a retry hook on fetch failure', async () => {
        const refresh = mountConfigYaml(host, {
            fetchConfigYaml: vi.fn().mockRejectedValue(new Error('500 boom')),
        });
        await refresh();
        expect(host.querySelector('[data-testid="config-yaml-error"]')).not.toBeNull();
        expect(host.textContent).toContain('500 boom');
    });

    it('shows the empty surface when backend returns blank YAML', async () => {
        const refresh = mountConfigYaml(host, {
            fetchConfigYaml: vi.fn().mockResolvedValue({ yaml: '' }),
        });
        await refresh();
        expect(host.querySelector('[data-testid="config-yaml-empty"]')).not.toBeNull();
    });

    it('surrounds the snippet with a "secrets redacted" hint in the header', async () => {
        const refresh = mountConfigYaml(host, {
            fetchConfigYaml: vi.fn().mockResolvedValue({ yaml: 'a: 1\n' }),
        });
        await refresh();
        expect(host.textContent).toContain('secrets redacted');
        expect(host.textContent).toContain('read-only');
    });
});
