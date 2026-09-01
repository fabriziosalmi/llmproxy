import { describe, it, expect, vi, beforeEach } from 'vitest';

// Mock only confirm() so the apply path doesn't need a modal click; keep every
// other primitive real so the form renders authentically.
vi.mock('../../ui', async (importOriginal) => {
    const actual = await importOriginal<typeof import('../../ui')>();
    return { ...actual, confirm: vi.fn(async () => true) };
});

import { mountGuidedConfig, type GuidedConfigApi } from './GuidedConfig';
import { getScalar } from './schema/yamlEdit';

const SAMPLE = `security:
  enabled: true
  max_payload_size_kb: 512
  confidence:
    regex_escalate_floor: 0.6
  link_sanitization:
    enabled: true
    blocked_domains: ["malicious-site.com"]
    homograph_protection:
      enabled: true
      log_only: false
      brands: ["paypal.com"]
    risk_scoring:
      enabled: false
      block_threshold: 0.7
      log_only: false
caching:
  enabled: true
  ttl: 3600
budget:
  daily_limit: 50.0
  soft_limit: 40.0
  fallback_to_local_on_limit: true
logging:
  level: "info"
  audit_trail:
    enabled: true
    mask_pii: true
`;

const flush = () => new Promise((r) => setTimeout(r, 0));

function makeApi(overrides: Partial<GuidedConfigApi> = {}): GuidedConfigApi {
    return {
        fetchConfigRaw: vi.fn(async () => ({ yaml: SAMPLE, path: 'config.yaml' })),
        validateConfig: vi.fn(async () => ({ valid: true, errors: [], warnings: [] })),
        applyConfig: vi.fn(async () => ({ applied: true, backup: 'config.yaml.bak.123' })),
        ...overrides,
    };
}

let host: HTMLElement;
beforeEach(() => {
    document.body.innerHTML = '';
    host = document.createElement('div');
    document.body.appendChild(host);
});

describe('mountGuidedConfig — rendering', () => {
    it('renders sections with inline help text', async () => {
        mountGuidedConfig(host, makeApi());
        await flush();
        expect(host.textContent).toContain('Security Shield');
        expect(host.textContent).toContain('Link & Homograph Protection');
        // an actual help sentence from the schema
        expect(host.textContent).toContain('Master switch for all prompt-injection');
    });

    it('reflects current on-disk values', async () => {
        mountGuidedConfig(host, makeApi());
        await flush();
        // security.enabled=true → switch aria-checked true
        const sw = host.querySelector('[data-testid="guided-security-enabled"]')!;
        expect(sw.getAttribute('aria-checked')).toBe('true');
        // number field prefilled
        const num = host.querySelector('#guided-security-max_payload_size_kb') as HTMLInputElement;
        expect(num.value).toBe('512');
        // chips reflect the brand list
        expect(
            host.querySelector('[data-testid="guided-security-link_sanitization-homograph_protection-brands"]')
                ?.textContent
        ).toContain('paypal.com');
    });

    it('hides advanced fields until the advanced toggle is on', async () => {
        mountGuidedConfig(host, makeApi());
        await flush();
        // risk_scoring.block_threshold is advanced → absent initially
        expect(host.querySelector('#guided-security-link_sanitization-risk_scoring-block_threshold')).toBeNull();
        const adv = host.querySelector('[data-testid="guided-advanced-toggle"]') as HTMLElement;
        adv.click();
        await flush();
        expect(host.querySelector('#guided-security-link_sanitization-risk_scoring-block_threshold')).not.toBeNull();
    });
});

describe('mountGuidedConfig — dirty tracking + save', () => {
    it('save is disabled until a field changes', async () => {
        mountGuidedConfig(host, makeApi());
        await flush();
        const save = host.querySelector('[data-testid="guided-save-btn"]') as HTMLButtonElement;
        expect(save.disabled).toBe(true);
        // flip homograph log_only false→true
        const sw = host.querySelector(
            '[data-testid="guided-security-link_sanitization-homograph_protection-log_only"]'
        ) as HTMLElement;
        sw.click();
        expect(save.disabled).toBe(false);
        expect(host.textContent).toContain('1 unsaved change');
    });

    it('applies only the changed leaf as merged YAML through validate+apply', async () => {
        const api = makeApi();
        mountGuidedConfig(host, api);
        await flush();

        // change: flip security.enabled true→false and edit budget.daily_limit
        (host.querySelector('[data-testid="guided-security-enabled"]') as HTMLElement).click();
        const dl = host.querySelector('#guided-budget-daily_limit') as HTMLInputElement;
        dl.value = '75';
        dl.dispatchEvent(new Event('input'));

        (host.querySelector('[data-testid="guided-save-btn"]') as HTMLButtonElement).click();
        await flush();
        await flush();

        expect(api.validateConfig).toHaveBeenCalledTimes(1);
        const sentYaml = (api.validateConfig as any).mock.calls[0][0] as string;
        // only the two changed leaves differ; everything else identical
        expect(getScalar(sentYaml, 'security.enabled')).toBe(false);
        expect(getScalar(sentYaml, 'budget.daily_limit')).toBe(75);
        expect(getScalar(sentYaml, 'caching.ttl')).toBe(3600); // untouched
        expect(sentYaml).toContain('blocked_domains: ["malicious-site.com"]'); // untouched line intact

        expect(api.applyConfig).toHaveBeenCalledTimes(1);
        expect((api.applyConfig as any).mock.calls[0][0]).toBe(sentYaml);
    });

    it('blocks apply on an out-of-range number and never calls the API', async () => {
        const api = makeApi();
        mountGuidedConfig(host, api);
        await flush();
        const floor = host.querySelector('#guided-security-confidence-regex_escalate_floor') as HTMLInputElement;
        floor.value = '5'; // schema max is 1
        floor.dispatchEvent(new Event('input'));
        (host.querySelector('[data-testid="guided-save-btn"]') as HTMLButtonElement).click();
        await flush();
        expect(api.validateConfig).not.toHaveBeenCalled();
        expect(host.textContent).toContain('Fix the highlighted');
    });

    it('exposes isDirty/dirtyCount and fires onDirty on edits', async () => {
        const onDirty = vi.fn();
        const h = mountGuidedConfig(host, makeApi(), undefined, { onDirty });
        await flush();
        expect(h.isDirty()).toBe(false);
        expect(h.dirtyCount()).toBe(0);
        onDirty.mockClear();
        (host.querySelector('[data-testid="guided-security-enabled"]') as HTMLElement).click();
        expect(h.isDirty()).toBe(true);
        expect(h.dirtyCount()).toBe(1);
        expect(onDirty).toHaveBeenLastCalledWith(1);
    });

    it('surfaces a server validation failure without applying', async () => {
        const api = makeApi({
            validateConfig: vi.fn(async () => ({ valid: false, errors: ['bad port'], warnings: [] })),
        });
        mountGuidedConfig(host, api);
        await flush();
        (host.querySelector('[data-testid="guided-caching-enabled"]') as HTMLElement).click();
        (host.querySelector('[data-testid="guided-save-btn"]') as HTMLButtonElement).click();
        await flush();
        await flush();
        expect(api.applyConfig).not.toHaveBeenCalled();
        expect(host.textContent).toContain('bad port');
    });
});
