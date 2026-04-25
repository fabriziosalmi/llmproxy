/**
 * Drawer primitive — replaces `services/drawer.js`.
 *
 * Slides in from the right edge of the page for non-modal investigation:
 * the explain pane (rule rationale) and the drilldown inspector both use
 * it. Differences vs Modal: long-lived (no promise), updatable while open
 * (`setTitle` / `setBody`), wider panel for tables and code blocks.
 *
 * Single-drawer model: calling `createDrawer()` while another is live
 * replaces the existing one's content rather than stacking. That keeps
 * repeated explain clicks from accumulating off-screen panels.
 *
 * Closes on Escape, backdrop click, the explicit ✕ button, or `.close()`
 * called by the owner. Focus is trapped inside the panel and restored to
 * the previously-focused element on close.
 */
import { cx } from './classnames';

export interface DrawerHandle {
    isOpen: boolean;
    setTitle(next: string): void;
    setBody(next: HTMLElement | string | null): void;
    close(): void;
}

export interface CreateDrawerOptions {
    title?: string;
    body?: HTMLElement | string | null;
    onClose?: () => void;
    /** Panel width in px. Capped at 95vw at runtime. */
    width?: number;
    testId?: string;
}

let _host: HTMLElement | null = null;
let _openHandle: DrawerHandle | null = null;
let _counter = 0;

function ensureHost(): HTMLElement {
    if (_host && document.body.contains(_host)) return _host;
    _host = document.createElement('div');
    _host.id = 'llmproxy-drawer-host';
    _host.className = 'fixed inset-0 z-[190] pointer-events-none';
    document.body.appendChild(_host);
    return _host;
}

function focusable(root: HTMLElement): HTMLElement[] {
    return Array.from(
        root.querySelectorAll<HTMLElement>(
            'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
        )
    ).filter((el) => !el.hasAttribute('aria-hidden'));
}

export function createDrawer(opts: CreateDrawerOptions = {}): DrawerHandle {
    const title = opts.title ?? '';
    const width = opts.width ?? 520;

    if (_openHandle && _openHandle.isOpen) {
        _openHandle.setTitle(title);
        _openHandle.setBody(opts.body ?? null);
        return _openHandle;
    }

    const host = ensureHost();
    const id = `drawer-${++_counter}`;
    const titleId = `${id}-title`;

    const backdrop = document.createElement('div');
    backdrop.className =
        'fixed inset-0 bg-[#050506]/70 backdrop-blur-sm pointer-events-auto opacity-0 transition-opacity duration-150';

    const panel = document.createElement('aside');
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-modal', 'true');
    panel.setAttribute('aria-labelledby', titleId);
    panel.style.width = `min(${width}px, 95vw)`;
    panel.className = cx(
        'fixed top-0 right-0 h-full bg-[#0a0a0c] border-l border-white/[0.08] shadow-2xl overflow-y-auto',
        'translate-x-full transition-transform duration-200 ease-out'
    );
    if (opts.testId) panel.setAttribute('data-testid', opts.testId);

    const header = document.createElement('header');
    header.className =
        'sticky top-0 z-10 bg-[#0a0a0c]/95 backdrop-blur-xl border-b border-white/[0.06] px-5 py-3 flex items-center justify-between';

    const heading = document.createElement('h2');
    heading.id = titleId;
    heading.className = 'text-xs font-bold text-white truncate';
    heading.textContent = title;

    const closeBtn = document.createElement('button');
    closeBtn.type = 'button';
    closeBtn.setAttribute('aria-label', 'Close drawer');
    closeBtn.setAttribute('data-testid', 'drawer-close');
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

    const prevFocus = document.activeElement as HTMLElement | null;

    let onKeyHandler: ((e: KeyboardEvent) => void) | null = null;

    const close = (): void => {
        if (!handle.isOpen) return;
        handle.isOpen = false;
        if (onKeyHandler) document.removeEventListener('keydown', onKeyHandler);
        backdrop.classList.add('opacity-0');
        panel.classList.add('translate-x-full');
        panel.classList.remove('translate-x-0');
        setTimeout(() => {
            backdrop.remove();
            panel.remove();
            if (_openHandle === handle) _openHandle = null;
            if (prevFocus && typeof prevFocus.focus === 'function') {
                try {
                    prevFocus.focus();
                } catch {
                    /* element gone */
                }
            }
            try {
                opts.onClose?.();
            } catch {
                /* swallow — caller is responsible for its own errors */
            }
        }, 200);
    };

    closeBtn.addEventListener('click', close);
    backdrop.addEventListener('click', close);

    onKeyHandler = (e: KeyboardEvent): void => {
        if (!handle.isOpen) return;
        if (e.key === 'Escape') {
            e.preventDefault();
            close();
            return;
        }
        if (e.key === 'Tab') {
            const f = focusable(panel);
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
        }
    };
    document.addEventListener('keydown', onKeyHandler);

    const handle: DrawerHandle = {
        isOpen: true,
        setTitle(next: string): void {
            heading.textContent = next;
        },
        setBody(next: HTMLElement | string | null): void {
            bodyEl.replaceChildren();
            if (next === null || next === undefined) return;
            if (next instanceof Node) bodyEl.appendChild(next);
            else if (typeof next === 'string') bodyEl.innerHTML = next;
        },
        close,
    };
    _openHandle = handle;

    // Slide in on the next frame so the transition runs.
    requestAnimationFrame(() => {
        backdrop.classList.remove('opacity-0');
        backdrop.classList.add('opacity-100');
        panel.classList.remove('translate-x-full');
        panel.classList.add('translate-x-0');
        setTimeout(() => closeBtn.focus(), 10);
    });

    handle.setBody(opts.body ?? null);
    return handle;
}
