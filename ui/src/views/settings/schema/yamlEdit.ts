/**
 * Surgical, comment-preserving YAML scalar get/set on the *raw* config.yaml text.
 *
 * The Guided Configuration form knows exactly which dotted paths it manages
 * (from configSchema), so it never needs a full YAML parser — it reads and
 * writes one scalar leaf at a time directly on the source text. That keeps the
 * on-disk config.yaml byte-for-byte intact (comments, ordering, unmanaged keys)
 * except for the single value line the user actually changed. Zero dependencies,
 * consistent with the rest of the codebase.
 *
 * Supported leaf value shapes: boolean, number, string, and a flat inline/block
 * list of strings (blocked_domains, homograph brands, …). Nested structural
 * sections (endpoints, model_groups) are out of scope — they stay in the raw
 * editor and their own dedicated views.
 *
 * Assumes block-style YAML with space indentation (config.yaml uses 2 spaces).
 * Not a general YAML engine: no anchors, flow maps, or multi-line scalars.
 */

const INDENT_STEP = 2;

export type Scalar = boolean | number | string | string[];

/** Number of leading spaces; tabs are treated as one column each (config uses spaces). */
function indentOf(line: string): number {
    let n = 0;
    while (n < line.length && line[n] === ' ') n++;
    return n;
}

function isBlankOrComment(line: string): boolean {
    const t = line.trim();
    return t === '' || t.startsWith('#');
}

/** The bare key of a `key: value` / `key:` line at the given indent, else null. */
function keyOf(line: string): string | null {
    const m = /^\s*("[^"]*"|'[^']*'|[^:#\s][^:]*?)\s*:(\s|$)/.exec(line);
    if (!m) return null;
    let k = m[1].trim();
    if ((k.startsWith('"') && k.endsWith('"')) || (k.startsWith("'") && k.endsWith("'"))) {
        k = k.slice(1, -1);
    }
    return k;
}

/**
 * Split a value-line's content after the colon into [rawValue, gap, comment].
 * A `#` only starts a comment when it is outside quotes/brackets and preceded by
 * whitespace (or at the very start of the value region) — so a `#` inside a URL
 * or a quoted string is preserved.
 */
function splitValueComment(afterColon: string): { rawValue: string; gap: string; comment: string } {
    let inS = false;
    let inD = false;
    let depth = 0;
    for (let i = 0; i < afterColon.length; i++) {
        const c = afterColon[i];
        if (c === "'" && !inD) inS = !inS;
        else if (c === '"' && !inS) inD = !inD;
        else if (!inS && !inD && (c === '[' || c === '{')) depth++;
        else if (!inS && !inD && (c === ']' || c === '}')) depth--;
        else if (c === '#' && !inS && !inD && depth <= 0) {
            const prev = i === 0 ? ' ' : afterColon[i - 1];
            if (prev === ' ' || prev === '\t') {
                const rawValue = afterColon.slice(0, i).replace(/\s+$/, '');
                const gap = afterColon.slice(rawValue.length, i);
                return { rawValue, gap, comment: afterColon.slice(i) };
            }
        }
    }
    return { rawValue: afterColon.replace(/\s+$/, ''), gap: '', comment: '' };
}

/** Parse a raw scalar token (already comment-stripped) into a typed value. */
export function parseScalarToken(raw: string): Scalar | null {
    const t = raw.trim();
    if (t === '' || t === '~' || t === 'null') return null;
    if (t === 'true' || t === 'True') return true;
    if (t === 'false' || t === 'False') return false;
    if (/^-?\d+(\.\d+)?$/.test(t)) return Number(t);
    if (t.startsWith('[') && t.endsWith(']')) {
        const inner = t.slice(1, -1).trim();
        if (inner === '') return [];
        return splitTopLevel(inner).map(unquote);
    }
    return unquote(t);
}

/** Split a flow-list body on top-level commas (respecting quotes). */
function splitTopLevel(s: string): string[] {
    const out: string[] = [];
    let cur = '';
    let inS = false;
    let inD = false;
    for (const c of s) {
        if (c === "'" && !inD) inS = !inS;
        else if (c === '"' && !inS) inD = !inD;
        if (c === ',' && !inS && !inD) {
            out.push(cur);
            cur = '';
        } else {
            cur += c;
        }
    }
    if (cur.trim() !== '') out.push(cur);
    return out;
}

function unquote(s: string): string {
    const t = s.trim();
    if ((t.startsWith('"') && t.endsWith('"')) || (t.startsWith("'") && t.endsWith("'"))) {
        return t.slice(1, -1);
    }
    return t;
}

/** True when a bare (unquoted) string is safe in YAML flow/scalar context. */
function isSafeBare(s: string): boolean {
    return /^[A-Za-z0-9_./-]+$/.test(s) && !/^(true|false|null|True|False|~)$/.test(s) && !/^-?\d+(\.\d+)?$/.test(s);
}

function formatScalarString(s: string): string {
    return isSafeBare(s) ? s : JSON.stringify(s);
}

/** Render a typed value as the YAML value token (no key, no comment). */
export function formatValue(value: Scalar): string {
    if (typeof value === 'boolean') return value ? 'true' : 'false';
    if (typeof value === 'number') return String(value);
    if (Array.isArray(value)) return '[' + value.map(formatScalarString).join(', ') + ']';
    return formatScalarString(value);
}

interface Located {
    lineIdx: number;
    /** Indent of the located key line. */
    indent: number;
}

/** Find the direct-child key `key` at `indent` within lines [start,end). */
function findChild(lines: string[], start: number, end: number, key: string, indent: number): Located | null {
    for (let i = start; i < end; i++) {
        const line = lines[i];
        if (isBlankOrComment(line)) continue;
        const ind = indentOf(line);
        if (ind < indent) break; // dedented out of the parent block
        if (ind === indent && keyOf(line) === key) return { lineIdx: i, indent: ind };
    }
    return null;
}

/** Exclusive end index of the child block under the key line at `keyIdx`. */
function blockEnd(lines: string[], keyIdx: number): number {
    const parentIndent = indentOf(lines[keyIdx]);
    for (let i = keyIdx + 1; i < lines.length; i++) {
        if (isBlankOrComment(lines[i])) continue;
        if (indentOf(lines[i]) <= parentIndent) return i;
    }
    return lines.length;
}

/** Indent of the first real child line under `keyIdx`, or parentIndent+step if none. */
function childIndent(lines: string[], keyIdx: number, end: number): number {
    const parentIndent = indentOf(lines[keyIdx]);
    for (let i = keyIdx + 1; i < end; i++) {
        if (isBlankOrComment(lines[i])) continue;
        return indentOf(lines[i]);
    }
    return parentIndent + INDENT_STEP;
}

interface Walk {
    /** Leaf key line, if the full path exists. */
    leafIdx: number | null;
    /** Deepest matched depth (# of path segments resolved to an existing key). */
    matchedDepth: number;
    /** [start,end) block range under the last matched ancestor (or whole doc). */
    rangeStart: number;
    rangeEnd: number;
    /** Child indent to use for the next (missing) segment. */
    childIndentAtRange: number;
}

function walk(lines: string[], segments: string[]): Walk {
    let rangeStart = 0;
    let rangeEnd = lines.length;
    let expectedIndent = 0;
    for (let d = 0; d < segments.length; d++) {
        const loc = findChild(lines, rangeStart, rangeEnd, segments[d], expectedIndent);
        if (!loc) {
            return {
                leafIdx: null,
                matchedDepth: d,
                rangeStart,
                rangeEnd,
                childIndentAtRange: expectedIndent,
            };
        }
        if (d === segments.length - 1) {
            return { leafIdx: loc.lineIdx, matchedDepth: d + 1, rangeStart, rangeEnd, childIndentAtRange: expectedIndent };
        }
        const end = blockEnd(lines, loc.lineIdx);
        expectedIndent = childIndent(lines, loc.lineIdx, end);
        rangeStart = loc.lineIdx + 1;
        rangeEnd = end;
    }
    // Unreachable for non-empty segments, but keep total.
    return { leafIdx: null, matchedDepth: segments.length, rangeStart, rangeEnd, childIndentAtRange: expectedIndent };
}

/** Read the current typed value at a dotted path, or `undefined` if absent. */
export function getScalar(text: string, path: string): Scalar | null | undefined {
    const lines = text.split('\n');
    const segments = path.split('.');
    const w = walk(lines, segments);
    if (w.leafIdx === null) return undefined;
    return readLeafValue(lines, w.leafIdx);
}

/** True when the path's leaf key exists in the text. */
export function hasPath(text: string, path: string): boolean {
    const lines = text.split('\n');
    return walk(lines, path.split('.')).leafIdx !== null;
}

function readLeafValue(lines: string[], leafIdx: number): Scalar | null {
    const line = lines[leafIdx];
    const colon = colonIndex(line);
    const afterColon = colon >= 0 ? line.slice(colon + 1) : '';
    const { rawValue } = splitValueComment(afterColon);
    if (rawValue.trim() !== '') return parseScalarToken(rawValue);
    // Block-style list: gather `- item` children.
    const end = blockEnd(lines, leafIdx);
    const items: string[] = [];
    for (let i = leafIdx + 1; i < end; i++) {
        const t = lines[i].trim();
        if (t.startsWith('- ')) items.push(unquote(t.slice(2).trim()));
        else if (t === '-') items.push('');
    }
    return items.length ? items : null;
}

/** Index of the structural `:` separating key from value (skips quoted keys). */
function colonIndex(line: string): number {
    let inS = false;
    let inD = false;
    for (let i = 0; i < line.length; i++) {
        const c = line[i];
        if (c === "'" && !inD) inS = !inS;
        else if (c === '"' && !inS) inD = !inD;
        else if (c === ':' && !inS && !inD) return i;
    }
    return -1;
}

/**
 * Set the scalar value at a dotted path, returning the new text. Preserves the
 * rest of the file (comments, ordering, other keys) verbatim. If the leaf (or
 * ancestor keys) don't exist, they are inserted under the deepest existing
 * ancestor at the correct indentation.
 */
export function setScalar(text: string, path: string, value: Scalar): string {
    const lines = text.split('\n');
    const segments = path.split('.');
    const w = walk(lines, segments);

    if (w.leafIdx !== null) {
        replaceLeaf(lines, w.leafIdx, value);
        return lines.join('\n');
    }

    // Insert the missing tail (segments from matchedDepth onward) at the end of
    // the deepest matched ancestor's block.
    const missing = segments.slice(w.matchedDepth);
    const baseIndent = w.childIndentAtRange;
    const insertAt = w.rangeEnd; // end of the ancestor block (or document)
    const block: string[] = [];
    for (let i = 0; i < missing.length; i++) {
        const ind = ' '.repeat(baseIndent + i * INDENT_STEP);
        if (i === missing.length - 1) {
            block.push(`${ind}${missing[i]}: ${formatValue(value)}`);
        } else {
            block.push(`${ind}${missing[i]}:`);
        }
    }
    lines.splice(insertAt, 0, ...block);
    return lines.join('\n');
}

/** Rewrite a leaf line's value in place, preserving any trailing comment. */
function replaceLeaf(lines: string[], leafIdx: number, value: Scalar): void {
    const line = lines[leafIdx];
    const colon = colonIndex(line);
    const head = line.slice(0, colon + 1); // includes the colon
    const afterColon = line.slice(colon + 1);
    const { rawValue, gap, comment } = splitValueComment(afterColon);

    if (rawValue.trim() === '') {
        // Was block-style (or empty) — drop any `- item` child lines and inline it.
        const end = blockEnd(lines, leafIdx);
        let removeTo = leafIdx + 1;
        for (let i = leafIdx + 1; i < end; i++) {
            const t = lines[i].trim();
            if (t.startsWith('-') || t === '') removeTo = i + 1;
            else break;
        }
        lines.splice(leafIdx + 1, removeTo - (leafIdx + 1));
    }

    const tail = comment ? `${gap || '  '}${comment}` : '';
    lines[leafIdx] = `${head} ${formatValue(value)}${tail}`;
}
