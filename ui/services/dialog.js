/**
 * In-app modal dialog primitives.
 *
 * Replaces native window.confirm() / window.prompt() with modals that
 * match the product's visual language, announce themselves as dialogs
 * (role="dialog" aria-modal="true") and trap focus until dismissed.
 *
 * Usage:
 *   const ok = await dialog.confirm({ title: 'Delete?', message: '…', danger: true });
 *   const val = await dialog.prompt({ title: 'API key', label: 'Key', inputType: 'password' });
 */

let _host = null;
let _counter = 0;

function _ensureHost() {
    if (_host && document.body.contains(_host)) return _host;
    _host = document.createElement('div');
    _host.id = 'llmproxy-dialog-host';
    _host.className = 'fixed inset-0 z-[200] pointer-events-none';
    document.body.appendChild(_host);
    return _host;
}

function _focusableDescendants(root) {
    return Array.from(root.querySelectorAll(
        'a[href], area[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    )).filter(el => !el.hasAttribute('aria-hidden'));
}

function _trap(root, firstFocus) {
    const prev = document.activeElement;
    const onKey = (e) => {
        if (e.key === 'Tab') {
            const f = _focusableDescendants(root);
            if (!f.length) { e.preventDefault(); return; }
            const first = f[0];
            const last = f[f.length - 1];
            if (e.shiftKey && document.activeElement === first) {
                e.preventDefault();
                last.focus();
            } else if (!e.shiftKey && document.activeElement === last) {
                e.preventDefault();
                first.focus();
            }
        }
    };
    root.addEventListener('keydown', onKey);
    setTimeout(() => (firstFocus || _focusableDescendants(root)[0] || root).focus(), 0);
    return () => {
        root.removeEventListener('keydown', onKey);
        // Restore focus to the element that opened the dialog (often the
        // button that triggered the confirm) so keyboard users don't land
        // at the top of the page.
        if (prev && typeof prev.focus === 'function') {
            try { prev.focus(); } catch { /* element gone */ }
        }
    };
}

function _open({ title, body, buttons, danger = false, initialFocus = null }) {
    const host = _ensureHost();
    const id = `dlg-${++_counter}`;
    const labelId = `${id}-title`;

    const backdrop = document.createElement('div');
    backdrop.className = 'fixed inset-0 bg-[#050506]/80 backdrop-blur-xl pointer-events-auto flex items-center justify-center animate-[fadeIn_0.12s_ease]';

    const panel = document.createElement('div');
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-modal', 'true');
    panel.setAttribute('aria-labelledby', labelId);
    panel.className = `w-full max-w-md mx-4 bg-[#0a0a0c] border ${danger ? 'border-rose-500/30' : 'border-white/[0.08]'} rounded-2xl p-5 shadow-2xl`;

    const heading = document.createElement('h2');
    heading.id = labelId;
    heading.className = `text-sm font-bold ${danger ? 'text-rose-400' : 'text-white'} mb-2`;
    heading.textContent = title;

    panel.appendChild(heading);
    if (body) panel.appendChild(body);

    const footer = document.createElement('div');
    footer.className = 'mt-5 flex justify-end gap-2';
    panel.appendChild(footer);

    return new Promise((resolve) => {
        const closeWith = (value) => {
            releaseTrap();
            backdrop.remove();
            resolve(value);
        };

        buttons.forEach(({ label, value, role }) => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.textContent = label;
            const base = 'px-3 py-1.5 rounded-lg text-[11px] font-bold transition-colors';
            if (role === 'primary') {
                btn.className = `primary-action ${base} ${danger ? 'bg-rose-500/20 hover:bg-rose-500/30 text-rose-300 border border-rose-500/40' : 'bg-cyan-500/20 hover:bg-cyan-500/30 text-cyan-300 border border-cyan-500/40'}`;
            } else {
                btn.className = `${base} text-slate-400 hover:text-white hover:bg-white/5`;
            }
            btn.addEventListener('click', () => {
                const v = typeof value === 'function' ? value() : value;
                closeWith(v);
            });
            footer.appendChild(btn);
        });

        backdrop.addEventListener('click', (e) => {
            if (e.target === backdrop) closeWith(undefined);
        });
        panel.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') { e.preventDefault(); closeWith(undefined); }
        });

        backdrop.appendChild(panel);
        host.appendChild(backdrop);

        const releaseTrap = _trap(panel, initialFocus);
    });
}

function _mkMessage(message) {
    const p = document.createElement('p');
    p.className = 'text-[12px] text-slate-400 leading-relaxed whitespace-pre-wrap';
    p.textContent = message;
    return p;
}

export const dialog = {
    /**
     * Modal confirmation.
     * @param {{ title: string, message?: string, confirmLabel?: string,
     *           cancelLabel?: string, danger?: boolean }} opts
     * @returns {Promise<boolean>} true if confirmed, false otherwise.
     */
    async confirm({ title, message = '', confirmLabel = 'Confirm', cancelLabel = 'Cancel', danger = false } = {}) {
        const body = _mkMessage(message);
        const result = await _open({
            title, body, danger,
            buttons: [
                { label: cancelLabel, value: false, role: 'ghost' },
                { label: confirmLabel, value: true, role: 'primary' },
            ],
        });
        return result === true;
    },

    /**
     * Modal single-line input.
     * @param {{ title: string, message?: string, label?: string,
     *           placeholder?: string, inputType?: string, defaultValue?: string,
     *           confirmLabel?: string, cancelLabel?: string,
     *           validate?: (v: string) => string|null }} opts
     * @returns {Promise<string|null>} the entered string, or null if cancelled.
     */
    async prompt({ title, message = '', label = '', placeholder = '', inputType = 'text',
                   defaultValue = '', confirmLabel = 'Continue', cancelLabel = 'Cancel',
                   validate = null } = {}) {
        const wrap = document.createElement('div');
        if (message) wrap.appendChild(_mkMessage(message));

        if (label) {
            const lb = document.createElement('label');
            lb.className = 'text-[9px] font-bold text-slate-500 uppercase tracking-widest mt-3 mb-1 block';
            lb.textContent = label;
            wrap.appendChild(lb);
        }

        const input = document.createElement('input');
        input.type = inputType;
        input.value = defaultValue;
        input.placeholder = placeholder;
        input.className = 'w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-xs text-white font-mono focus:border-cyan-500/50';

        const err = document.createElement('p');
        err.className = 'hidden mt-2 text-[10px] text-rose-400 font-mono';
        err.setAttribute('role', 'alert');

        wrap.appendChild(input);
        wrap.appendChild(err);

        const readValue = () => {
            const v = input.value;
            if (validate) {
                const msg = validate(v);
                if (msg) {
                    input.setAttribute('aria-invalid', 'true');
                    err.textContent = msg;
                    err.classList.remove('hidden');
                    return undefined; // keep dialog open
                }
            }
            return v;
        };

        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                const btn = input.closest('[role="dialog"]').querySelector('button.primary-action');
                if (btn) btn.click();
            }
        });

        const result = await _open({
            title, body: wrap, initialFocus: input,
            buttons: [
                { label: cancelLabel, value: null, role: 'ghost' },
                { label: confirmLabel, value: readValue, role: 'primary' },
            ],
        });

        // Primary button passes `readValue` which may return undefined to
        // keep the dialog open on validation failure. In that path _open
        // resolves `undefined` — detect and re-open.
        if (result === undefined) {
            return this.prompt({
                title, message, label, placeholder, inputType,
                defaultValue: input.value, confirmLabel, cancelLabel, validate,
            });
        }
        return result;
    },
};
