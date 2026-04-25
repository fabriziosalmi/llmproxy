import { describe, expect, it } from 'vitest';
import { EMBEDDING_PREFIXES, isEmbeddingModel, providerColor } from './types';

describe('isEmbeddingModel', () => {
    it('matches known embedding prefixes case-insensitively', () => {
        for (const prefix of EMBEDDING_PREFIXES) {
            expect(isEmbeddingModel(`${prefix}-foo`)).toBe(true);
            expect(isEmbeddingModel(`${prefix.toUpperCase()}-FOO`)).toBe(true);
        }
    });

    it('rejects chat models', () => {
        expect(isEmbeddingModel('gpt-4o-mini')).toBe(false);
        expect(isEmbeddingModel('claude-sonnet-4')).toBe(false);
        expect(isEmbeddingModel('llama-3.3-70b-instruct')).toBe(false);
    });
});

describe('providerColor', () => {
    it('returns the bespoke palette for known providers', () => {
        expect(providerColor('openai')).toContain('emerald');
        expect(providerColor('anthropic')).toContain('amber');
        expect(providerColor('google')).toContain('sky');
    });

    it('case-insensitive lookup', () => {
        expect(providerColor('OpenAI')).toBe(providerColor('openai'));
    });

    it('falls back to slate for unknown providers', () => {
        expect(providerColor('made-up-vendor')).toContain('slate');
    });
});
