/**
 * Snippet — code block with a copy-to-clipboard button in the corner.
 *
 * Pure DOM, no syntax highlighter. Operators don't need rainbow tokens
 * to copy a 5-line cURL — they need the copy to land in the clipboard
 * and the visual feedback to say "yes it copied". The "language" prop
 * is used for the corner tag + an aria-label, not parsing.
 */

import { cx } from './classnames';

export interface SnippetOptions {
    /** Visible language tag (e.g. 'cURL', 'Python', 'TypeScript'). */
    language: string;
    /** Code body — rendered inside <pre><code>, whitespace preserved. */
    code: string;
    /** Optional one-line caption above the block. */
    caption?: string;
    className?: string;
    testId?: string;
}

export interface SnippetHandle {
    root: HTMLElement;
    /** Programmatically copy the snippet — returns true on success. */
    copy(): Promise<boolean>;
}

const ICON_COPY =
    '<svg viewBox="0 0 16 16" width="11" height="11" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">' +
    '<rect x="5" y="5" width="9" height="9" rx="1.5"/><path d="M3 11V3a1 1 0 0 1 1-1h7"/></svg>';
const ICON_OK =
    '<svg viewBox="0 0 16 16" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">' +
    '<path d="m3 8 3 3 7-7" stroke-linecap="round" stroke-linejoin="round"/></svg>';

async function _copyText(text: string): Promise<boolean> {
    if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
        try {
            await navigator.clipboard.writeText(text);
            return true;
        } catch {
            /* fall through to legacy path */
        }
    }
    // Legacy fallback — works on http:// and older browsers where the
    // Clipboard API isn't permitted.
    if (typeof document === 'undefined') return false;
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.position = 'absolute';
    ta.style.left = '-9999px';
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try {
        ok = document.execCommand('copy');
    } catch {
        ok = false;
    }
    document.body.removeChild(ta);
    return ok;
}

export function createSnippet(opts: SnippetOptions): SnippetHandle {
    const root = document.createElement('div');
    root.className = cx(
        'relative bg-white/[0.02] border border-white/[0.06] rounded-xl overflow-hidden',
        opts.className
    );
    if (opts.testId) root.setAttribute('data-testid', opts.testId);

    if (opts.caption) {
        const cap = document.createElement('p');
        cap.className = 'text-[10px] text-slate-500 px-3 pt-2';
        cap.textContent = opts.caption;
        root.appendChild(cap);
    }

    // Header bar with the language tag (left) + copy button (right).
    const header = document.createElement('div');
    header.className = 'flex items-center justify-between px-3 py-1.5 border-b border-white/[0.04] bg-white/[0.01]';
    const lang = document.createElement('span');
    lang.className = 'text-[9px] font-bold text-slate-500 uppercase tracking-widest';
    lang.textContent = opts.language;
    header.appendChild(lang);

    const copyBtn = document.createElement('button');
    copyBtn.type = 'button';
    copyBtn.setAttribute('aria-label', `Copy ${opts.language} snippet to clipboard`);
    copyBtn.className = cx(
        'inline-flex items-center gap-1 text-[10px] font-mono text-slate-500 hover:text-cyan-300',
        'px-2 py-0.5 rounded transition-colors',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500/40'
    );
    copyBtn.innerHTML = `${ICON_COPY}<span>copy</span>`;
    if (opts.testId) copyBtn.setAttribute('data-testid', `${opts.testId}-copy`);
    header.appendChild(copyBtn);
    root.appendChild(header);

    const pre = document.createElement('pre');
    pre.className = 'm-0 px-3 py-3 text-[11px] leading-relaxed font-mono text-slate-200 overflow-x-auto';
    const code = document.createElement('code');
    code.textContent = opts.code;
    pre.appendChild(code);
    root.appendChild(pre);

    let resetTimer: ReturnType<typeof setTimeout> | null = null;
    const flashCopied = (success: boolean): void => {
        if (resetTimer) clearTimeout(resetTimer);
        copyBtn.innerHTML = success ? `${ICON_OK}<span>copied</span>` : `${ICON_COPY}<span>failed</span>`;
        copyBtn.classList.toggle('text-emerald-300', success);
        copyBtn.classList.toggle('text-rose-300', !success);
        resetTimer = setTimeout(() => {
            copyBtn.innerHTML = `${ICON_COPY}<span>copy</span>`;
            copyBtn.classList.remove('text-emerald-300', 'text-rose-300');
            resetTimer = null;
        }, 1_500);
    };

    const doCopy = async (): Promise<boolean> => {
        const ok = await _copyText(opts.code);
        flashCopied(ok);
        return ok;
    };

    copyBtn.addEventListener('click', () => {
        void doCopy();
    });

    return { root, copy: doCopy };
}
