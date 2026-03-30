/**
 * Lightweight toast notification system.
 * Replaces native alert()/confirm() with glassmorphic toasts (audit #10/#11).
 */

const COLORS = {
    success: { bg: 'bg-emerald-500/10', border: 'border-emerald-500/20', text: 'text-emerald-400' },
    error:   { bg: 'bg-rose-500/10',    border: 'border-rose-500/20',    text: 'text-rose-400' },
    warning: { bg: 'bg-amber-500/10',    border: 'border-amber-500/20',   text: 'text-amber-400' },
    info:    { bg: 'bg-sky-500/10',      border: 'border-sky-500/20',     text: 'text-sky-400' },
};

/**
 * Show a toast notification.
 * @param {string} message - Text to display
 * @param {'success'|'error'|'warning'|'info'} type - Visual style
 * @param {number} duration - Auto-dismiss in ms (0 = sticky)
 */
export function toast(message, type = 'info', duration = 3500) {
    const container = document.getElementById('toast-container');
    if (!container) return;

    const c = COLORS[type] || COLORS.info;
    const el = document.createElement('div');
    el.className = `toast ${c.bg} ${c.border} border backdrop-blur-xl rounded-xl px-4 py-3 flex items-start gap-3 shadow-lg`;
    el.innerHTML = `
        <span class="text-[11px] font-semibold ${c.text} leading-relaxed flex-1">${_esc(message)}</span>
        <button class="text-slate-500 hover:text-white text-xs shrink-0 mt-0.5">&times;</button>
    `;

    // Close on click
    el.querySelector('button').addEventListener('click', () => _dismiss(el));

    container.appendChild(el);

    if (duration > 0) {
        setTimeout(() => _dismiss(el), duration);
    }
}

function _dismiss(el) {
    if (el._removing) return;
    el._removing = true;
    el.classList.add('removing');
    el.addEventListener('animationend', () => el.remove());
}

function _esc(s) {
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
}
