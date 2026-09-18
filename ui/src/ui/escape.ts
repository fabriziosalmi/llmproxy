/**
 * HTML escaping for the one place this codebase still builds markup as strings.
 *
 * Every legacy component that renders backend data defines its own escaper —
 * registry.js, models.js, plugins.js, threats.js and chat.js each have one — so
 * the discipline was clearly understood. The TypeScript rewrite that replaced
 * and extended those views dropped it: drilldown.ts is 940 lines with 31
 * innerHTML assignments and contained no entity replacement anywhere, and
 * explain.ts named its unescaped local `safeVal`.
 *
 * That mattered because the data is not ours. An inference caller chooses the
 * `model` string; it is written to audit_log and rendered back into the
 * operator's console, so `_kv('Model', r.model)` is a path from an ordinary
 * /v1/chat/completions request to markup in the admin origin. Script execution
 * was blocked — the UI CSP is `script-src 'self'` with no unsafe-inline — but
 * that is one layer where there should be two, and CSP is not a substitute for
 * escaping the value.
 *
 * Five copies of an escaper is also how one of them ends up subtly different.
 * This is the single one; the legacy files can adopt it as they are touched.
 */

/**
 * Escape `value` for interpolation into an HTML text or attribute context.
 *
 * Uses the DOM's own serialiser rather than a hand-written entity table, which
 * is what the existing per-file helpers do and is harder to get wrong. Falls
 * back to explicit replacement where there is no document (SSR, a worker, or a
 * test environment without happy-dom).
 */
export function escapeHtml(value: unknown): string {
    const raw = value === null || value === undefined ? '' : String(value);
    if (typeof document !== 'undefined') {
        const el = document.createElement('div');
        el.textContent = raw;
        // textContent → innerHTML escapes &, < and >. Quotes are handled below
        // so the result is safe inside a double-quoted attribute too, which the
        // DOM path alone does not guarantee.
        return el.innerHTML.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }
    return raw
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

/**
 * Tagged template that escapes every interpolated value.
 *
 *     html`<span>${untrusted}</span>`
 *
 * Literal chunks pass through, so surrounding markup is written normally and
 * the only thing that can inject is a value — which is escaped by construction
 * rather than by the author remembering.
 */
export function html(strings: TemplateStringsArray, ...values: unknown[]): string {
    return strings.reduce((out, chunk, i) => out + chunk + (i < values.length ? escapeHtml(values[i]) : ''), '');
}
