import { describe, expect, it } from 'vitest';
import { getHashParam, hashTab, hashSub, setHashParams, setHashView } from '../services/urlstate.js';

describe('urlstate hash helpers', () => {
    it('reads the current tab without query params', () => {
        window.location.hash = '#/models?q=gpt';
        expect(hashTab()).toBe('models');
        expect(getHashParam('q')).toBe('gpt');
    });

    it('reads the tab as the FIRST segment of a multi-segment view', () => {
        window.location.hash = '#/settings/traffic';
        expect(hashTab()).toBe('settings');
        expect(hashSub()).toBe('traffic');
    });

    it('hashSub is empty for a single-segment view', () => {
        window.location.hash = '#/settings';
        expect(hashSub()).toBe('');
    });

    it('setHashView writes #/<tab>/<sub> preserving ?params', () => {
        window.location.hash = '#/settings/config?tr=24h';
        setHashView('settings', 'traffic');
        expect(window.location.hash).toBe('#/settings/traffic?tr=24h');
        expect(hashTab()).toBe('settings');
        expect(hashSub()).toBe('traffic');
        expect(getHashParam('tr')).toBe('24h');
    });

    it('setHashView with no sub writes a bare #/<tab>', () => {
        window.location.hash = '#/settings/traffic';
        setHashView('analytics');
        expect(window.location.hash).toBe('#/analytics');
        expect(hashSub()).toBe('');
    });

    it('updates one param without dropping existing params', () => {
        window.location.hash = '#/logs?tr=24h&log_q=blocked';
        setHashParams({ log_level: 'ERROR' });
        expect(window.location.hash).toContain('tr=24h');
        expect(window.location.hash).toContain('log_q=blocked');
        expect(window.location.hash).toContain('log_level=ERROR');
    });

    it('removes empty params', () => {
        window.location.hash = '#/models?q=gpt&tr=24h';
        setHashParams({ q: null });
        expect(getHashParam('q')).toBeNull();
        expect(getHashParam('tr')).toBe('24h');
    });
});
