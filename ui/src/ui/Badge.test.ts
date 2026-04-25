import { describe, expect, it } from 'vitest';
import { createBadge } from './Badge';

describe('Badge', () => {
    it('renders neutral by default', () => {
        const b = createBadge({ label: 'idle' });
        expect(b.tagName).toBe('SPAN');
        expect(b.textContent).toContain('idle');
        expect(b.className).toContain('bg-white/5');
        expect(b.title).toBe('idle');
    });

    it('switches palette per intent', () => {
        const danger = createBadge({ label: 'BLOCKED', intent: 'danger' });
        const success = createBadge({ label: 'OK', intent: 'success' });
        expect(danger.className).toContain('bg-red-500/15');
        expect(success.className).toContain('bg-emerald-500/15');
    });

    it('renders an indicator dot when dot=true', () => {
        const b = createBadge({ label: 'live', intent: 'success', dot: true });
        const dot = b.querySelector('[aria-hidden="true"]');
        expect(dot).not.toBeNull();
        expect(dot?.className).toContain('bg-emerald-400');
    });

    it('uses provided title attribute when supplied', () => {
        const b = createBadge({ label: 'PII…', title: 'Personally Identifiable Information masked' });
        expect(b.title).toBe('Personally Identifiable Information masked');
    });
});
