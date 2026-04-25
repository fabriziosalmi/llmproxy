/**
 * Tiny class-list composer. Accepts strings, falsy values, and objects whose
 * keys are appended only when their value is truthy. Always returns a stable,
 * deduplicated, space-separated string.
 *
 * @example
 *   cx('btn', { 'btn--lg': size === 'lg' }, isOpen && 'is-open')
 */
export type ClassValue = string | number | false | null | undefined | ClassDict | ClassValue[];
type ClassDict = { [key: string]: unknown };

export function cx(...inputs: ClassValue[]): string {
    const out: string[] = [];
    for (const input of inputs) {
        if (!input) continue;
        if (typeof input === 'string' || typeof input === 'number') {
            out.push(String(input));
        } else if (Array.isArray(input)) {
            const nested = cx(...input);
            if (nested) out.push(nested);
        } else if (typeof input === 'object') {
            for (const key of Object.keys(input)) {
                if ((input as ClassDict)[key]) out.push(key);
            }
        }
    }
    // Deduplicate while preserving order
    const seen = new Set<string>();
    const result: string[] = [];
    for (const cls of out.join(' ').split(/\s+/)) {
        if (cls && !seen.has(cls)) {
            seen.add(cls);
            result.push(cls);
        }
    }
    return result.join(' ');
}
