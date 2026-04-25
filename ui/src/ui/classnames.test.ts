import { describe, expect, it } from 'vitest';
import { cx } from './classnames';

describe('cx()', () => {
    it('joins strings with a single space', () => {
        expect(cx('a', 'b', 'c')).toBe('a b c');
    });

    it('drops falsy values', () => {
        expect(cx('a', false, null, undefined, 0, '', 'b')).toBe('a b');
    });

    it('appends object keys only when their value is truthy', () => {
        expect(cx('btn', { 'btn--lg': true, 'btn--disabled': false, 'is-open': 1 })).toBe('btn btn--lg is-open');
    });

    it('flattens nested arrays', () => {
        expect(cx('a', ['b', ['c', { d: true }]])).toBe('a b c d');
    });

    it('deduplicates while preserving first-seen order', () => {
        expect(cx('a', 'b', 'a', 'c', 'b')).toBe('a b c');
    });

    it('handles whitespace inside provided strings without producing empties', () => {
        expect(cx('a  b', '  c   d  ')).toBe('a b c d');
    });
});
