import { describe, expect, it } from 'vitest';
import { getHashParam, hashTab, setHashParams } from '../services/urlstate.js';

describe('urlstate hash helpers', () => {
    it('reads the current tab without query params', () => {
        window.location.hash = '#/models?q=gpt';
        expect(hashTab()).toBe('models');
        expect(getHashParam('q')).toBe('gpt');
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
