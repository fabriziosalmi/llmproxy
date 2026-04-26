import { describe, expect, it, vi } from 'vitest';
import { createGuardCard } from './GuardCard';
import type { GuardSpec } from './types';

const TOGGLEABLE: GuardSpec = {
    key: 'injection_guard',
    name: 'Injection Guard',
    iconSvg: '<svg></svg>',
    description: 'Blocks "ignore previous instructions".',
    toggleable: true,
    intent: 'primary',
    provenance: 'Triggered on prompt body ingress. Pattern set lives in plugins/ring2/injection_guard.py.',
};

const STATIC: GuardSpec = {
    key: 'pii_masker',
    name: 'PII Masker',
    iconSvg: '<svg></svg>',
    description: 'Masks emails, phones, SSNs, credit cards.',
    toggleable: false,
    staticStatus: 'ALWAYS ON',
    intent: 'primary',
    provenance: 'Required by data-protection guarantees.',
};

describe('createGuardCard', () => {
    it('renders the name, description, and the ACTIVE state line when enabled', () => {
        const card = createGuardCard({ spec: TOGGLEABLE, enabled: true });
        expect(card.textContent).toContain('Injection Guard');
        expect(card.textContent).toContain('ignore previous instructions');
        expect(card.textContent).toContain('ACTIVE');
        expect(card.textContent).not.toContain('DISABLED');
    });

    it('flips to DISABLED state line when disabled', () => {
        const card = createGuardCard({ spec: TOGGLEABLE, enabled: false });
        expect(card.textContent).toContain('DISABLED');
        expect(card.textContent).not.toContain('ACTIVE');
    });

    it('toggleable spec renders a role=switch button with the right testid; firing it calls onToggle', async () => {
        // N.3: GuardCard's toggle is debounced 200 ms, so the click fires the
        // visual flip immediately but the onToggle callback runs trailing.
        vi.useFakeTimers();
        const onToggle = vi.fn();
        const card = createGuardCard({ spec: TOGGLEABLE, enabled: false, onToggle });
        const sw = card.querySelector<HTMLButtonElement>('[data-testid="guard-toggle-injection_guard"]');
        expect(sw).not.toBeNull();
        expect(sw?.getAttribute('aria-checked')).toBe('false');
        sw!.click();
        // Visual state flipped synchronously; callback hasn't fired yet.
        expect(sw?.getAttribute('aria-checked')).toBe('true');
        expect(onToggle).not.toHaveBeenCalled();
        vi.advanceTimersByTime(200);
        expect(onToggle).toHaveBeenCalledWith(true);
        vi.useRealTimers();
    });

    it('static spec renders a status badge with the static label, not a toggle', () => {
        const card = createGuardCard({ spec: STATIC, enabled: true });
        expect(card.querySelector('[role="switch"]')).toBeNull();
        const badge = card.querySelector('[data-testid="guard-status-pii_masker"]');
        expect(badge).not.toBeNull();
        expect(badge?.textContent).toContain('ALWAYS ON');
    });

    it('statusOverride wins over the static label (firewall surfaces "OFF · env" with the reason)', () => {
        const card = createGuardCard({
            spec: STATIC,
            enabled: false,
            statusOverride: 'OFF · env:LLM_PROXY_FIREWALL_ENABLED',
        });
        const badge = card.querySelector('[data-testid="guard-status-pii_masker"]');
        expect(badge?.textContent).toContain('OFF · env');
    });

    it('has a provenance ℹ button with aria-label tied to the guard name', () => {
        const card = createGuardCard({ spec: TOGGLEABLE, enabled: true });
        const info = card.querySelector('button[aria-label="About Injection Guard"]');
        expect(info).not.toBeNull();
    });
});
