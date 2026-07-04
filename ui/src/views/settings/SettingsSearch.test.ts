import { describe, it, expect, vi, beforeEach } from 'vitest';
import { mountSettingsSearch, searchFields, sectionTitleOf } from './SettingsSearch';
import { ALL_FIELDS } from './schema/configSchema';

describe('searchFields', () => {
    it('returns nothing for an empty query', () => {
        expect(searchFields('')).toEqual([]);
        expect(searchFields('   ')).toEqual([]);
    });

    it('finds the homograph brands field by label term', () => {
        const r = searchFields('brands');
        expect(r.length).toBeGreaterThan(0);
        expect(r.map((f) => f.path)).toContain('security.link_sanitization.homograph_protection.brands');
    });

    it('finds by path/section term too (e.g. cache)', () => {
        const paths = searchFields('cache').map((f) => f.path);
        expect(paths.some((p) => p.startsWith('caching.'))).toBe(true);
    });

    it('ranks a label prefix match above a help-only match', () => {
        const r = searchFields('log');
        // "Log level" (label prefix) should outrank fields that only mention "log" in help
        expect(r[0].label.toLowerCase().startsWith('log')).toBe(true);
    });

    it('requires all terms to match (AND semantics)', () => {
        expect(searchFields('homograph zzzznope')).toEqual([]);
    });

    it('caps the result count', () => {
        expect(searchFields('e', ALL_FIELDS, 3).length).toBeLessThanOrEqual(3);
    });
});

describe('sectionTitleOf', () => {
    it('maps a path to its section title', () => {
        expect(sectionTitleOf('security.enabled')).toBe('Security Shield');
        expect(sectionTitleOf('caching.ttl')).toBe('Caching');
    });
});

describe('mountSettingsSearch', () => {
    let host: HTMLElement;
    beforeEach(() => {
        document.body.innerHTML = '';
        host = document.createElement('div');
        document.body.appendChild(host);
    });

    const input = () => host.querySelector('[data-testid="settings-search-input"]') as HTMLInputElement;
    const results = () => host.querySelector('#settings-search-results') as HTMLElement;

    it('shows matching results as you type', () => {
        mountSettingsSearch(host, { openField: vi.fn() });
        input().value = 'homograph';
        input().dispatchEvent(new Event('input'));
        expect(results().classList.contains('hidden')).toBe(false);
        expect(results().textContent).toContain('Protected brands');
    });

    it('calls openField with the field path on click', () => {
        const openField = vi.fn();
        mountSettingsSearch(host, { openField });
        input().value = 'brands';
        input().dispatchEvent(new Event('input'));
        const opt = host.querySelector(
            '[data-testid="settings-search-opt-security.link_sanitization.homograph_protection.brands"]'
        ) as HTMLElement;
        opt.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
        expect(openField).toHaveBeenCalledWith('security.link_sanitization.homograph_protection.brands');
        // input cleared + results closed
        expect(input().value).toBe('');
        expect(results().classList.contains('hidden')).toBe(true);
    });

    it('navigates with arrows and opens with Enter', () => {
        const openField = vi.fn();
        mountSettingsSearch(host, { openField });
        input().value = 'cache';
        input().dispatchEvent(new Event('input'));
        input().dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
        input().dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
        expect(openField).toHaveBeenCalledTimes(1);
        expect(openField.mock.calls[0][0]).toMatch(/^caching\./);
    });

    it('Escape clears and closes', () => {
        mountSettingsSearch(host, { openField: vi.fn() });
        input().value = 'homograph';
        input().dispatchEvent(new Event('input'));
        input().dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
        expect(input().value).toBe('');
        expect(results().classList.contains('hidden')).toBe(true);
    });
});
