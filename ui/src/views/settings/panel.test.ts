import { describe, it, expect } from 'vitest';
import { settingsPanel } from './panel';

describe('settingsPanel', () => {
    it('renders the canonical card class (rounded-2xl, p-6) for consistency', () => {
        const p = settingsPanel({ body: document.createElement('div') });
        expect(p.className).toContain('rounded-2xl');
        expect(p.className).toContain('p-6');
        expect(p.className).toContain('bg-white/[0.03]');
    });

    it('renders a title and forwards the testId', () => {
        const p = settingsPanel({
            title: 'Webhooks',
            body: document.createElement('div'),
            testId: 'settings-webhooks',
        });
        expect(p.getAttribute('data-testid')).toBe('settings-webhooks');
        expect(p.querySelector('h2')?.textContent).toBe('Webhooks');
    });

    it('places a titleRight action in the heading row', () => {
        const btn = document.createElement('button');
        btn.textContent = 'Test Fire';
        const p = settingsPanel({ title: 'Webhooks', titleRight: btn, body: document.createElement('div') });
        const head = p.querySelector('h2')!.parentElement!;
        expect(head.contains(btn)).toBe(true);
    });

    it('accepts an array body and appends every node', () => {
        const a = document.createElement('div');
        a.id = 'a';
        const b = document.createElement('div');
        b.id = 'b';
        const p = settingsPanel({ body: [a, b] });
        expect(p.querySelector('#a')).not.toBeNull();
        expect(p.querySelector('#b')).not.toBeNull();
    });

    it('omits the heading row when no title is given', () => {
        const p = settingsPanel({ body: document.createElement('div') });
        expect(p.querySelector('h2')).toBeNull();
    });
});
