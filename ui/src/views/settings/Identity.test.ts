import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mountIdentity } from './Identity';

let host: HTMLElement;

beforeEach(() => {
    host = document.createElement('div');
    document.body.appendChild(host);
});

afterEach(() => {
    host.remove();
});

describe('mountIdentity', () => {
    it('renders Auth Mode = SSO / OIDC + identity-me grid when authenticated', async () => {
        const refresh = mountIdentity(host, {
            fetchIdentityConfig: vi.fn().mockResolvedValue({ enabled: true }),
            fetchIdentityMe: vi.fn().mockResolvedValue({
                authenticated: true,
                provider: 'okta',
                email: 'fab@example.com',
                roles: ['admin', 'auditor'],
                permissions: ['read:audit', 'write:keys'],
            }),
        });
        await refresh();
        expect(host.textContent).toContain('SSO / OIDC');
        expect(host.textContent).toContain('Enabled');
        expect(host.textContent).toContain('okta');
        expect(host.textContent).toContain('fab@example.com');
        expect(host.textContent).toContain('admin');
        expect(host.textContent).toContain('2 granted');
        expect(host.querySelector('[data-testid="identity-me"]')).not.toBeNull();
    });

    it('renders an empty-state when /identity/me reports unauthenticated', async () => {
        const refresh = mountIdentity(host, {
            fetchIdentityConfig: vi.fn().mockResolvedValue({ enabled: false }),
            fetchIdentityMe: vi.fn().mockResolvedValue({ authenticated: false }),
        });
        await refresh();
        expect(host.textContent).toContain('API Key');
        expect(host.textContent).toContain('Disabled');
        expect(host.querySelector('[data-testid="identity-me-empty"]')).not.toBeNull();
    });

    it('falls back to "unknown" when /identity/config 503s', async () => {
        const refresh = mountIdentity(host, {
            fetchIdentityConfig: vi.fn().mockRejectedValue(new Error('503')),
            fetchIdentityMe: vi.fn().mockRejectedValue(new Error('503')),
        });
        await refresh();
        expect(host.textContent).toContain('unknown');
        expect(host.textContent).toContain('Identity service unavailable');
    });
});
