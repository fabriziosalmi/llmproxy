import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mountWebhooks } from './Webhooks';

let host: HTMLElement;

beforeEach(() => {
    host = document.createElement('div');
    document.body.appendChild(host);
});

afterEach(() => {
    host.remove();
});

describe('mountWebhooks', () => {
    it('renders configured endpoints with a target badge per row', async () => {
        const handle = mountWebhooks(host, {
            fetchWebhooks: vi.fn().mockResolvedValue({
                enabled: true,
                endpoints: [
                    { name: 'sec-channel', target: 'slack', events: ['injection_blocked', 'panic'] },
                    { name: 'soc-msteams', target: 'teams', events: ['critical_alert'] },
                ],
                event_types: ['injection_blocked', 'panic', 'critical_alert', 'budget_exceeded'],
            }),
            testWebhook: vi.fn(),
        });
        await handle.refresh();
        expect(host.textContent).toContain('sec-channel');
        expect(host.textContent).toContain('soc-msteams');
        expect(host.querySelector('[data-testid="webhook-target-sec-channel"]')?.textContent).toContain('SLACK');
        expect(host.textContent).toContain('budget_exceeded');
    });

    it('renders an empty-state when webhooks are disabled in config.yaml', async () => {
        const handle = mountWebhooks(host, {
            fetchWebhooks: vi.fn().mockResolvedValue({ enabled: false }),
            testWebhook: vi.fn(),
        });
        await handle.refresh();
        expect(host.querySelector('[data-testid="webhooks-disabled"]')).not.toBeNull();
    });

    it('Test Fire button calls testWebhook and surfaces a success toast', async () => {
        const testWebhook = vi.fn().mockResolvedValue(undefined);
        const toast = vi.fn();
        const handle = mountWebhooks(
            host,
            {
                fetchWebhooks: vi.fn().mockResolvedValue({ enabled: true, endpoints: [], event_types: [] }),
                testWebhook,
            },
            toast
        );
        await handle.refresh();
        const btn = host.querySelector<HTMLButtonElement>('[data-testid="test-webhook-btn"]')!;
        btn.click();
        await new Promise((r) => setTimeout(r, 0));
        expect(testWebhook).toHaveBeenCalled();
        expect(toast).toHaveBeenCalledWith(expect.stringContaining('Test webhook'), 'success');
    });

    it('Test Fire surfaces an error toast when the call rejects', async () => {
        const testWebhook = vi.fn().mockRejectedValue(new Error('500 dispatcher offline'));
        const toast = vi.fn();
        const handle = mountWebhooks(
            host,
            {
                fetchWebhooks: vi.fn().mockResolvedValue({ enabled: true, endpoints: [], event_types: [] }),
                testWebhook,
            },
            toast
        );
        await handle.refresh();
        const btn = host.querySelector<HTMLButtonElement>('[data-testid="test-webhook-btn"]')!;
        btn.click();
        await new Promise((r) => setTimeout(r, 0));
        expect(toast).toHaveBeenCalledWith(expect.stringContaining('500'), 'error');
    });

    it('shows ErrorState with retry when /webhooks 503s', async () => {
        const handle = mountWebhooks(host, {
            fetchWebhooks: vi.fn().mockRejectedValue(new Error('500')),
            testWebhook: vi.fn(),
        });
        await handle.refresh();
        expect(host.querySelector('[data-testid="webhooks-error"]')).not.toBeNull();
    });
});
