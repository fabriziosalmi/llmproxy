import { describe, it, expect, beforeEach, vi } from 'vitest';
import { mountSettingsTabs, tabFromHash, type SettingsTabSpec } from './SettingsTabs';

const TABS: SettingsTabSpec[] = [
    { id: 'config', label: 'Configuration' },
    { id: 'access', label: 'Access & Identity' },
    { id: 'system', label: 'System & Data' },
];

let root: HTMLElement;
let bar: HTMLElement;

beforeEach(() => {
    document.body.innerHTML = '';
    root = document.createElement('div');
    for (const t of TABS) {
        const sec = document.createElement('section');
        sec.id = `settings-sec-${t.id}`;
        sec.textContent = `${t.label} content`;
        root.appendChild(sec);
    }
    bar = document.createElement('div');
    document.body.append(bar, root);
});

const sec = (id: string) => root.querySelector<HTMLElement>(`#settings-sec-${id}`)!;
const tabBtn = (id: string) => bar.querySelector<HTMLButtonElement>(`[data-testid="settingstab-${id}"]`)!;

describe('tabFromHash', () => {
    it('parses #settings/<id> and rejects anything else', () => {
        expect(tabFromHash('#settings/config')).toBe('config');
        expect(tabFromHash('#settings/access')).toBe('access');
        expect(tabFromHash('#other')).toBeNull();
        expect(tabFromHash('')).toBeNull();
    });
});

describe('mountSettingsTabs', () => {
    it('renders a tab per section and shows only the first by default', () => {
        mountSettingsTabs(bar, TABS, { root, useHash: false });
        expect(bar.querySelectorAll('[role="tab"]').length).toBe(3);
        expect(sec('config').classList.contains('hidden')).toBe(false);
        expect(sec('access').classList.contains('hidden')).toBe(true);
        expect(sec('system').classList.contains('hidden')).toBe(true);
        // ARIA wiring
        expect(tabBtn('config').getAttribute('aria-selected')).toBe('true');
        expect(sec('config').getAttribute('role')).toBe('tabpanel');
        expect(sec('config').getAttribute('aria-labelledby')).toBe('settingstab-config');
    });

    it('switches sections instantly on click', () => {
        const h = mountSettingsTabs(bar, TABS, { root, useHash: false });
        tabBtn('system').click();
        expect(h.getActive()).toBe('system');
        expect(sec('system').classList.contains('hidden')).toBe(false);
        expect(sec('config').classList.contains('hidden')).toBe(true);
        expect(tabBtn('system').getAttribute('aria-selected')).toBe('true');
        expect(tabBtn('config').getAttribute('aria-selected')).toBe('false');
    });

    it('honors initial when provided', () => {
        const h = mountSettingsTabs(bar, TABS, { root, useHash: false, initial: 'access' });
        expect(h.getActive()).toBe('access');
        expect(sec('access').classList.contains('hidden')).toBe(false);
    });

    it('walks tabs with arrow keys (wrapping) and Home/End', () => {
        const h = mountSettingsTabs(bar, TABS, { root, useHash: false });
        tabBtn('config').dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true }));
        expect(h.getActive()).toBe('system'); // wrap to last
        tabBtn('system').dispatchEvent(new KeyboardEvent('keydown', { key: 'Home', bubbles: true }));
        expect(h.getActive()).toBe('config');
        tabBtn('config').dispatchEvent(new KeyboardEvent('keydown', { key: 'End', bubbles: true }));
        expect(h.getActive()).toBe('system');
    });

    it('fires onChange only on a real change', () => {
        const onChange = vi.fn();
        const h = mountSettingsTabs(bar, TABS, { root, useHash: false, onChange });
        h.setActive('config'); // already active → no fire
        expect(onChange).not.toHaveBeenCalled();
        h.setActive('access');
        expect(onChange).toHaveBeenCalledWith('access');
    });

    it('skips tabs whose section is missing', () => {
        sec('system').remove();
        mountSettingsTabs(bar, TABS, { root, useHash: false });
        expect(bar.querySelectorAll('[role="tab"]').length).toBe(2);
        expect(tabBtn('system')).toBeNull();
    });

    it('reads and writes the deep-link hash', () => {
        location.hash = '#settings/access';
        const h = mountSettingsTabs(bar, TABS, { root, useHash: true });
        expect(h.getActive()).toBe('access'); // hash wins over default
        h.setActive('system');
        expect(location.hash).toBe('#settings/system');
        h.destroy();
        location.hash = '';
    });
});
