/**
 * The TypeScript views render backend data as HTML strings and contained no
 * escaper at all, while every legacy JS component that renders the same data
 * defines one. drilldown.ts is 940 lines with 31 innerHTML assignments;
 * explain.ts named its unescaped local `safeVal`.
 *
 * The path that matters: an inference caller picks the `model` string in a
 * /v1/chat/completions body, it is written to audit_log, and the drilldown
 * renders it back into the operator's console.
 */
import { describe, expect, it } from 'vitest';

import { escapeHtml, html } from './escape';

describe('escapeHtml', () => {
    it('neutralises tag syntax', () => {
        expect(escapeHtml('<img src=x onerror=alert(1)>')).not.toContain('<img');
        expect(escapeHtml('<script>')).toBe('&lt;script&gt;');
    });

    it('escapes quotes so the result is safe inside an attribute', () => {
        expect(escapeHtml('" onmouseover="x')).not.toContain('"');
        expect(escapeHtml("' onmouseover='x")).not.toContain("'");
    });

    it('escapes ampersands so entities cannot be smuggled', () => {
        expect(escapeHtml('&lt;script&gt;')).toBe('&amp;lt;script&amp;gt;');
    });

    it('renders null and undefined as empty rather than the words', () => {
        expect(escapeHtml(null)).toBe('');
        expect(escapeHtml(undefined)).toBe('');
    });

    it('leaves ordinary values alone', () => {
        expect(escapeHtml('gpt-4o')).toBe('gpt-4o');
        expect(escapeHtml(42)).toBe('42');
    });

    it('handles the model string an inference caller would choose', () => {
        const attack = '</span><img src=x onerror="fetch(`/api/v1/config/raw`)">';
        const out = escapeHtml(attack);
        expect(out).not.toContain('<img');
        expect(out).not.toContain('</span>');
    });
});

describe('html tagged template', () => {
    it('escapes interpolated values but not the literal markup', () => {
        const out = html`<span>${'<b>x</b>'}</span>`;
        expect(out).toBe('<span>&lt;b&gt;x&lt;/b&gt;</span>');
    });

    it('escapes every value, not just the first', () => {
        const out = html`${'<a>'}|${'<b>'}`;
        expect(out).toBe('&lt;a&gt;|&lt;b&gt;');
    });
});
