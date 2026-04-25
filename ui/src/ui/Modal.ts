/**
 * Modal primitive — replaces `services/dialog.js`.
 *
 * Three entry points:
 *   - `createModal(opts)` for general-purpose modals (returns a promise that
 *     resolves with the value of the chosen button).
 *   - `confirm(opts)` for yes/no prompts → Promise<boolean>.
 *   - `prompt(opts)` for single-line input → Promise<string | null>.
 *
 * All variants share: role=dialog + aria-modal=true, focus trap, Escape /
 * backdrop-click cancellation, focus restoration to the previously-focused
 * element on close, and a danger=true accent for destructive actions.
 */
import { cx } from './classnames';

export type ButtonRole = 'primary' | 'ghost';

export interface ModalButton<T = unknown> {
    label: string;
    /** Value passed to the resolver. May be a function — return undefined to keep the modal open (e.g. on validation failure). */
    value: T | (() => T | undefined);
    role?: ButtonRole;
    testId?: string;
}

export interface CreateModalOptions<T = unknown> {
    title: string;
    body?: HTMLElement | HTMLElement[] | string;
    buttons: ModalButton<T>[];
    danger?: boolean;
    /** Element to focus after the modal opens. Defaults to the first focusable descendant. */
    initialFocus?: HTMLElement | null;
    /** Forwarded to data-testid on the dialog panel. */
    testId?: string;
}

export interface ConfirmOptions {
    title: string;
    message?: string;
    confirmLabel?: string;
    cancelLabel?: string;
    danger?: boolean;
}

export interface PromptOptions {
    title: string;
    message?: string;
    label?: string;
    placeholder?: string;
    inputType?: 'text' | 'password' | 'email' | 'number' | 'url';
    defaultValue?: string;
    confirmLabel?: string;
    cancelLabel?: string;
    /** Sync validation. Return null when valid; return a message to keep the modal open and show it as an error. */
    validate?: (v: string) => string | null;
}

let _host: HTMLElement | null = null;
let _counter = 0;

function ensureHost(): HTMLElement {
    if (_host && document.body.contains(_host)) return _host;
    _host = document.createElement('div');
    _host.id = 'llmproxy-modal-host';
    _host.className = 'fixed inset-0 z-[200] pointer-events-none';
    document.body.appendChild(_host);
    return _host;
}

function focusableDescendants(root: HTMLElement): HTMLElement[] {
    return Array.from(
        root.querySelectorAll<HTMLElement>(
            'a[href], area[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
        )
    ).filter((el) => !el.hasAttribute('aria-hidden'));
}

function trapFocus(root: HTMLElement, firstFocus: HTMLElement | null | undefined): () => void {
    const prev = document.activeElement as HTMLElement | null;
    const onKey = (e: KeyboardEvent): void => {
        if (e.key !== 'Tab') return;
        const f = focusableDescendants(root);
        if (!f.length) {
            e.preventDefault();
            return;
        }
        const first = f[0]!;
        const last = f[f.length - 1]!;
        if (e.shiftKey && document.activeElement === first) {
            e.preventDefault();
            last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
            e.preventDefault();
            first.focus();
        }
    };
    root.addEventListener('keydown', onKey);
    setTimeout(() => (firstFocus ?? focusableDescendants(root)[0] ?? root).focus(), 0);
    return () => {
        root.removeEventListener('keydown', onKey);
        if (prev && typeof prev.focus === 'function') {
            try {
                prev.focus();
            } catch {
                /* element gone */
            }
        }
    };
}

const BUTTON_BASE = 'px-3 py-1.5 rounded-lg text-[11px] font-bold transition-colors';
const BUTTON_PRIMARY = 'primary-action bg-cyan-500/20 hover:bg-cyan-500/30 text-cyan-300 border border-cyan-500/40';
const BUTTON_PRIMARY_DANGER =
    'primary-action bg-rose-500/20 hover:bg-rose-500/30 text-rose-300 border border-rose-500/40';
const BUTTON_GHOST = 'text-slate-400 hover:text-white hover:bg-white/5';

export function createModal<T = unknown>(opts: CreateModalOptions<T>): Promise<T | undefined> {
    const host = ensureHost();
    const id = `dlg-${++_counter}`;
    const labelId = `${id}-title`;

    const backdrop = document.createElement('div');
    backdrop.className =
        'fixed inset-0 bg-[#050506]/80 backdrop-blur-xl pointer-events-auto flex items-center justify-center animate-[fadeIn_0.12s_ease]';

    const panel = document.createElement('div');
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-modal', 'true');
    panel.setAttribute('aria-labelledby', labelId);
    panel.className = cx(
        'w-full max-w-md mx-4 bg-[#0a0a0c] border rounded-2xl p-5 shadow-2xl',
        opts.danger ? 'border-rose-500/30' : 'border-white/[0.08]'
    );
    if (opts.testId) panel.setAttribute('data-testid', opts.testId);

    const heading = document.createElement('h2');
    heading.id = labelId;
    heading.className = cx('text-sm font-bold mb-2', opts.danger ? 'text-rose-400' : 'text-white');
    heading.textContent = opts.title;
    panel.appendChild(heading);

    if (opts.body !== undefined) {
        if (typeof opts.body === 'string') {
            const p = document.createElement('p');
            p.className = 'text-[12px] text-slate-400 leading-relaxed whitespace-pre-wrap';
            p.textContent = opts.body;
            panel.appendChild(p);
        } else if (Array.isArray(opts.body)) {
            for (const child of opts.body) panel.appendChild(child);
        } else {
            panel.appendChild(opts.body);
        }
    }

    const footer = document.createElement('div');
    footer.className = 'mt-5 flex justify-end gap-2';
    panel.appendChild(footer);

    return new Promise<T | undefined>((resolve) => {
        let releaseTrap: () => void = () => {};

        const closeWith = (value: T | undefined): void => {
            releaseTrap();
            backdrop.remove();
            resolve(value);
        };

        for (const btn of opts.buttons) {
            const el = document.createElement('button');
            el.type = 'button';
            el.textContent = btn.label;
            el.className = cx(
                BUTTON_BASE,
                btn.role === 'primary' ? (opts.danger ? BUTTON_PRIMARY_DANGER : BUTTON_PRIMARY) : BUTTON_GHOST
            );
            if (btn.testId) el.setAttribute('data-testid', btn.testId);
            el.addEventListener('click', () => {
                const v = typeof btn.value === 'function' ? (btn.value as () => T | undefined)() : btn.value;
                if (v === undefined && typeof btn.value === 'function') {
                    // Validation failed — keep the modal open.
                    return;
                }
                closeWith(v as T | undefined);
            });
            footer.appendChild(el);
        }

        backdrop.addEventListener('click', (e) => {
            if (e.target === backdrop) closeWith(undefined);
        });
        panel.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                e.preventDefault();
                closeWith(undefined);
            }
        });

        backdrop.appendChild(panel);
        host.appendChild(backdrop);
        releaseTrap = trapFocus(panel, opts.initialFocus ?? null);
    });
}

export async function confirm(opts: ConfirmOptions): Promise<boolean> {
    const result = await createModal<boolean>({
        title: opts.title,
        body: opts.message,
        danger: opts.danger ?? false,
        testId: 'modal-confirm',
        buttons: [
            { label: opts.cancelLabel ?? 'Cancel', value: false, role: 'ghost', testId: 'modal-confirm-cancel' },
            { label: opts.confirmLabel ?? 'Confirm', value: true, role: 'primary', testId: 'modal-confirm-ok' },
        ],
    });
    return result === true;
}

export async function prompt(opts: PromptOptions): Promise<string | null> {
    return new Promise((resolve) => {
        const wrap = document.createElement('div');

        if (opts.message) {
            const p = document.createElement('p');
            p.className = 'text-[12px] text-slate-400 leading-relaxed whitespace-pre-wrap';
            p.textContent = opts.message;
            wrap.appendChild(p);
        }

        if (opts.label) {
            const lb = document.createElement('label');
            lb.className = 'text-[9px] font-bold text-slate-500 uppercase tracking-widest mt-3 mb-1 block';
            lb.textContent = opts.label;
            wrap.appendChild(lb);
        }

        const input = document.createElement('input');
        input.type = opts.inputType ?? 'text';
        input.value = opts.defaultValue ?? '';
        input.placeholder = opts.placeholder ?? '';
        input.className =
            'w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-xs text-white font-mono focus:border-cyan-500/50';
        input.setAttribute('data-testid', 'modal-prompt-input');

        const errEl = document.createElement('p');
        errEl.className = 'hidden mt-2 text-[10px] text-rose-400 font-mono';
        errEl.setAttribute('role', 'alert');

        wrap.appendChild(input);
        wrap.appendChild(errEl);

        const readValue = (): string | undefined => {
            const v = input.value;
            if (opts.validate) {
                const msg = opts.validate(v);
                if (msg) {
                    input.setAttribute('aria-invalid', 'true');
                    errEl.textContent = msg;
                    errEl.classList.remove('hidden');
                    return undefined;
                }
            }
            return v;
        };

        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                const btn = (input.closest('[role="dialog"]') as HTMLElement | null)?.querySelector<HTMLButtonElement>(
                    'button.primary-action'
                );
                btn?.click();
            }
        });

        void createModal<string | null>({
            title: opts.title,
            body: wrap,
            initialFocus: input,
            testId: 'modal-prompt',
            buttons: [
                {
                    label: opts.cancelLabel ?? 'Cancel',
                    value: null,
                    role: 'ghost',
                    testId: 'modal-prompt-cancel',
                },
                {
                    label: opts.confirmLabel ?? 'Continue',
                    value: () => readValue() ?? undefined,
                    role: 'primary',
                    testId: 'modal-prompt-ok',
                },
            ],
        }).then((v) => {
            // createModal resolves undefined when the user dismissed without a value
            // (Escape / backdrop). We map that to null to match dialog.js semantics.
            resolve(v === undefined ? null : v);
        });
    });
}
