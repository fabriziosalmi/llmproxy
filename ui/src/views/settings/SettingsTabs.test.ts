import { describe, it, expect, beforeEach, vi } from 'vitest';
import { mountSettingsTabs, type SettingsTabSpec } from './SettingsTabs';

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

    it('reads and writes the app-convention deep-link hash (#/settings/<sub>)', () => {
        location.hash = '#/settings/access';
        const h = mountSettingsTabs(bar, TABS, { root, useHash: true });
        expect(h.getActive()).toBe('access'); // hash wins over default
        h.setActive('system');
        expect(location.hash).toBe('#/settings/system');
        h.destroy();
        location.hash = '';
    });

    it('canonicalizes a bare #/settings to #/settings/<first-tab> on mount', () => {
        location.hash = '#/settings';
        const h = mountSettingsTabs(bar, TABS, { root, useHash: true });
        expect(h.getActive()).toBe('config'); // first tab
        expect(location.hash).toBe('#/settings/config');
        h.destroy();
        location.hash = '';
    });

    it('preserves ?params when writing the sub-tab hash', () => {
        location.hash = '#/settings/config?tr=24h';
        const h = mountSettingsTabs(bar, TABS, { root, useHash: true });
        h.setActive('system');
        expect(location.hash).toBe('#/settings/system?tr=24h');
        h.destroy();
        location.hash = '';
    });
});

describe('mountSettingsTabs — live badges', () => {
    const badge = (id: string) => bar.querySelector<HTMLElement>(`[data-testid="settingstab-${id}-badge"]`)!;

    it('sets a text badge, a dot badge, and clears it', () => {
        const h = mountSettingsTabs(bar, TABS, { root, useHash: false });
        expect(badge('config').children.length).toBe(0);

        h.setBadge('config', { text: '3', intent: 'warning' });
        expect(badge('config').textContent).toBe('3');

        h.setBadge('config', { dot: true, intent: 'warning' });
        expect(badge('config').textContent).toBe('');
        expect(badge('config').querySelector('span')).not.toBeNull();

        h.setBadge('config', null);
        expect(badge('config').children.length).toBe(0);
    });

    it('ignores a badge for an unknown tab', () => {
        const h = mountSettingsTabs(bar, TABS, { root, useHash: false });
        expect(() => h.setBadge('nope', { text: '1' })).not.toThrow();
    });
});

describe('mountSettingsTabs — guard (unsaved-changes veto)', () => {
    const flush = () => new Promise((r) => setTimeout(r, 0));

    it('vetoes a switch when the guard returns false', async () => {
        const guard = vi.fn(async () => false);
        const h = mountSettingsTabs(bar, TABS, { root, useHash: false, guard });
        tabBtn('system').click();
        await flush();
        expect(guard).toHaveBeenCalledWith('config', 'system');
        expect(h.getActive()).toBe('config'); // stayed
        expect(sec('config').classList.contains('hidden')).toBe(false);
    });

    it('allows the switch when the guard returns true', async () => {
        const guard = vi.fn(async () => true);
        const h = mountSettingsTabs(bar, TABS, { root, useHash: false, guard });
        tabBtn('system').click();
        await flush();
        expect(h.getActive()).toBe('system');
    });

    it('does not consult the guard when re-selecting the active tab', async () => {
        const guard = vi.fn(async () => true);
        const h = mountSettingsTabs(bar, TABS, { root, useHash: false, guard });
        tabBtn('config').click(); // already active
        await flush();
        expect(guard).not.toHaveBeenCalled();
        expect(h.getActive()).toBe('config');
    });

    it('restores the hash when a hash-triggered switch is vetoed', async () => {
        location.hash = '#/settings/config';
        const guard = vi.fn(async () => false);
        const h = mountSettingsTabs(bar, TABS, { root, useHash: true, guard });
        expect(h.getActive()).toBe('config');
        location.hash = '#/settings/system'; // deep-link / Cmd+K jump
        await flush();
        await flush();
        expect(h.getActive()).toBe('config'); // vetoed
        expect(location.hash).toBe('#/settings/config'); // restored
        h.destroy();
        location.hash = '';
    });
});
