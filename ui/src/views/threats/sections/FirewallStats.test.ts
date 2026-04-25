import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { renderFirewallStats } from './FirewallStats';

let host: HTMLElement;

beforeEach(() => {
    host = document.createElement('div');
    document.body.appendChild(host);
});

afterEach(() => {
    host.remove();
});

describe('renderFirewallStats', () => {
    it('renders scanned + blocked counts', () => {
        renderFirewallStats(host, { firewall: { total_scanned: 1234, total_blocked: 7 } });
        expect(host.querySelector('[data-testid="firewall-scanned"]')?.textContent).toBe('1,234');
        expect(host.querySelector('[data-testid="firewall-blocked"]')?.textContent).toBe('7');
    });

    it('blocked counter is rose when > 0, emerald when 0', () => {
        renderFirewallStats(host, { firewall: { total_scanned: 100, total_blocked: 5 } });
        expect(host.querySelector('[data-testid="firewall-blocked"]')?.className).toContain('rose-400');
        host.replaceChildren();
        renderFirewallStats(host, { firewall: { total_scanned: 100, total_blocked: 0 } });
        expect(host.querySelector('[data-testid="firewall-blocked"]')?.className).toContain('emerald-400');
    });

    it('renders the signature breakdown when present', () => {
        renderFirewallStats(host, {
            firewall: {
                total_scanned: 50,
                total_blocked: 3,
                block_by_signature: { 'sig:rate_429': 2, 'sig:bot_ua': 1 },
            },
        });
        const sigs = host.querySelector('[data-testid="firewall-signatures"]')!;
        expect(sigs.textContent).toContain('sig:rate_429');
        expect(sigs.textContent).toContain('2x');
        expect(sigs.textContent).toContain('sig:bot_ua');
    });

    it('skips the signature breakdown when none are reported', () => {
        renderFirewallStats(host, { firewall: { total_scanned: 5, total_blocked: 0 } });
        expect(host.querySelector('[data-testid="firewall-signatures"]')).toBeNull();
    });
});
