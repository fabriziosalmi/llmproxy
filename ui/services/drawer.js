/**
 * Side-drawer primitive for non-modal investigation.
 *
 * Used by explain (status rationale) and drilldown (entity inspector).
 * Slides in from the right, dims page behind, traps keyboard focus,
 * closes on Escape / backdrop click / explicit close button.
 *
 * Differences vs dialog.js:
 *   - Not a promise-based confirm/prompt — long-lived inspection surface.
 *   - Can be updated in place (setBody) while open, so async fetches
 *     can populate sections progressively.
 *   - Wider panel (up to 520px) for tables + code blocks.
 *
 * Usage:
 *   const d = drawer.open({ title: 'Endpoint · ollama-auto', body: initialNode });
 *   d.setBody(newNode);
 *   d.close();
 */

let _host = null;
let _openHandle = null;
let _counter = 0;

function _ensureHost() {
    if (_host && document.body.contains(_host)) return _host;
    _host = document.createElement('div');
    _host.id = 'llmproxy-drawer-host';
    _host.className = 'fixed inset-0 z-[190] pointer-events-none';
    document.body.appendChild(_host);
    return _host;
}

function _focusable(root) {
    return Array.from(root.querySelectorAll(
        'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    )).filter(el => !el.hasAttribute('aria-hidden'));
}

/**
 * Open a side drawer. Returns a handle with .setBody(node), .setTitle(text), .close().
 * Calling open() while another drawer is live replaces its content (single-drawer model).
 */
export const drawer = {
    open({ title = '', body = null, onClose = null, width = 520 } = {}) {
        // Single-drawer: re-use the existing panel if one is live so multiple
        // explain clicks don't stack up off-screen.
        if (_openHandle && _openHandle.isOpen) {
            _openHandle.setTitle(title);
            _openHandle.setBody(body);
            return _openHandle;
        }

        const host = _ensureHost();
        const id = `drawer-${++_counter}`;
        const titleId = `${id}-title`;

        const backdrop = document.createElement('div');
        backdrop.className = 'fixed inset-0 bg-[#050506]/70 backdrop-blur-sm pointer-events-auto opacity-0 transition-opacity duration-150';

        const panel = document.createElement('aside');
        panel.setAttribute('role', 'dialog');
        panel.setAttribute('aria-modal', 'true');
        panel.setAttribute('aria-labelledby', titleId);
        panel.style.width = `min(${width}px, 95vw)`;
        panel.className = 'fixed top-0 right-0 h-full bg-[#0a0a0c] border-l border-white/[0.08] shadow-2xl overflow-y-auto translate-x-full transition-transform duration-200 ease-out';

        const header = document.createElement('header');
        header.className = 'sticky top-0 z-10 bg-[#0a0a0c]/95 backdrop-blur-xl border-b border-white/[0.06] px-5 py-3 flex items-center justify-between';

        const heading = document.createElement('h2');
        heading.id = titleId;
        heading.className = 'text-xs font-bold text-white truncate';
        heading.textContent = title;

        const closeBtn = document.createElement('button');
        closeBtn.type = 'button';
        closeBtn.setAttribute('aria-label', 'Close drawer');
        closeBtn.className = 'text-slate-500 hover:text-white text-base leading-none px-2 py-1 rounded hover:bg-white/5';
        closeBtn.innerHTML = '&times;';

        const bodyEl = document.createElement('div');
        bodyEl.className = 'px-5 py-4 text-[12px] text-slate-300';

        header.appendChild(heading);
        header.appendChild(closeBtn);
        panel.appendChild(header);
        panel.appendChild(bodyEl);
        host.appendChild(backdrop);
        host.appendChild(panel);

        const prevFocus = document.activeElement;

        const close = () => {
            if (!handle.isOpen) return;
            handle.isOpen = false;
            onKeyHandler && document.removeEventListener('keydown', onKeyHandler);
            backdrop.classList.add('opacity-0');
            panel.classList.add('translate-x-full');
            panel.classList.remove('translate-x-0');
            setTimeout(() => {
                backdrop.remove();
                panel.remove();
                if (_openHandle === handle) _openHandle = null;
                if (prevFocus && typeof prevFocus.focus === 'function') {
                    try { prevFocus.focus(); } catch { /* element gone */ }
                }
                try { onClose && onClose(); } catch { /* ignore */ }
            }, 200);
        };

        closeBtn.addEventListener('click', close);
        backdrop.addEventListener('click', close);

        // Focus trap + Escape. Document-level so focus anywhere in the page
        // still honors the trap while the drawer is the topmost surface.
        const onKeyHandler = (e) => {
            if (!handle.isOpen) return;
            if (e.key === 'Escape') { e.preventDefault(); close(); return; }
            if (e.key === 'Tab') {
                const f = _focusable(panel);
                if (!f.length) { e.preventDefault(); return; }
                const first = f[0];
                const last = f[f.length - 1];
                if (e.shiftKey && document.activeElement === first) {
                    e.preventDefault(); last.focus();
                } else if (!e.shiftKey && document.activeElement === last) {
                    e.preventDefault(); first.focus();
                }
            }
        };
        document.addEventListener('keydown', onKeyHandler);

        const handle = {
            isOpen: true,
            setTitle(next) { heading.textContent = next; },
            setBody(next) {
                bodyEl.innerHTML = '';
                if (next instanceof Node) bodyEl.appendChild(next);
                else if (typeof next === 'string') bodyEl.innerHTML = next;
            },
            close,
        };
        _openHandle = handle;

        // Slide in on next frame so the transition runs.
        requestAnimationFrame(() => {
            backdrop.classList.remove('opacity-0');
            backdrop.classList.add('opacity-100');
            panel.classList.remove('translate-x-full');
            panel.classList.add('translate-x-0');
            const target = closeBtn;
            setTimeout(() => target.focus(), 10);
        });

        handle.setBody(body);
        return handle;
    },
};
