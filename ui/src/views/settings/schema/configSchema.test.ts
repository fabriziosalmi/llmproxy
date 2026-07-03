import { describe, it, expect } from 'vitest';
import { CONFIG_SECTIONS, ALL_FIELDS, fieldError } from './configSchema';

describe('configSchema integrity', () => {
    it('has unique, dotted, non-empty paths', () => {
        const paths = ALL_FIELDS.map((f) => f.path);
        expect(new Set(paths).size).toBe(paths.length);
        for (const p of paths) expect(p).toMatch(/^[a-z_]+(\.[a-z_]+)+$/);
    });

    it('every field carries a label and a real help sentence', () => {
        for (const f of ALL_FIELDS) {
            expect(f.label.length).toBeGreaterThan(0);
            expect(f.help.length).toBeGreaterThan(20); // no empty/placeholder help
        }
    });

    it('enum fields have options and a default that is a valid option', () => {
        for (const f of ALL_FIELDS.filter((x) => x.type === 'enum')) {
            expect(f.options && f.options.length).toBeGreaterThan(0);
            const vals = (f.options ?? []).map((o) => o.value);
            expect(vals).toContain(f.default);
        }
    });

    it('number fields with bounds have min ≤ default ≤ max', () => {
        for (const f of ALL_FIELDS.filter((x) => x.type === 'number')) {
            if (typeof f.default === 'number') {
                if (f.min != null) expect(f.default).toBeGreaterThanOrEqual(f.min);
                if (f.max != null) expect(f.default).toBeLessThanOrEqual(f.max);
            }
        }
    });

    it('boolean/stringList defaults are the right JS type', () => {
        for (const f of ALL_FIELDS) {
            if (f.type === 'boolean' && f.default !== undefined) expect(typeof f.default).toBe('boolean');
            if (f.type === 'stringList' && f.default !== undefined) expect(Array.isArray(f.default)).toBe(true);
        }
    });

    it('sections have unique ids and at least one field', () => {
        const ids = CONFIG_SECTIONS.map((s) => s.id);
        expect(new Set(ids).size).toBe(ids.length);
        for (const s of CONFIG_SECTIONS) expect(s.fields.length).toBeGreaterThan(0);
    });

    it('covers the homograph knobs we just shipped', () => {
        const paths = ALL_FIELDS.map((f) => f.path);
        expect(paths).toContain('security.link_sanitization.homograph_protection.brands');
        expect(paths).toContain('security.link_sanitization.homograph_protection.log_only');
    });
});

describe('fieldError', () => {
    const num = ALL_FIELDS.find((f) => f.path === 'security.confidence.regex_escalate_floor')!;
    const en = ALL_FIELDS.find((f) => f.type === 'enum')!;

    it('accepts in-range numbers and rejects out-of-range', () => {
        expect(fieldError(num, 0.6)).toBeNull();
        expect(fieldError(num, 1.5)).toMatch(/≤/);
        expect(fieldError(num, -0.1)).toMatch(/≥/);
        expect(fieldError(num, 'abc')).toMatch(/number/);
    });

    it('validates enum membership', () => {
        expect(fieldError(en, en.options![0].value)).toBeNull();
        expect(fieldError(en, 'not-an-option')).toMatch(/one of/);
    });
});
