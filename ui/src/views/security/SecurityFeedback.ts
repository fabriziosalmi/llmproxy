function applyText(el: HTMLElement | null, text: string, className: string): void {
    if (!el) return;
    el.textContent = text;
    el.className = className;
}

export function renderVerifyPending(el: HTMLElement | null): void {
    applyText(el, 'Verifying chain...', 'font-mono text-[10px] text-slate-400');
}

export function renderVerifyValid(el: HTMLElement | null, verified: number): void {
    applyText(
        el,
        `Chain valid — ${verified} entries verified, 0 tampering detected`,
        'font-mono text-[10px] text-emerald-400'
    );
}

export function renderVerifyBroken(el: HTMLElement | null, brokenAt: number, error?: string): void {
    applyText(
        el,
        `CHAIN BROKEN at entry #${brokenAt} — ${error || 'tamper detected'}`,
        'font-mono text-[10px] text-rose-400'
    );
}

export function renderVerifyError(el: HTMLElement | null, error: string): void {
    applyText(el, `Error: ${error}`, 'font-mono text-[10px] text-rose-400');
}

export function renderChainStatus(el: HTMLElement | null, valid: boolean): void {
    if (!el) return;
    el.textContent = valid ? 'VALID' : 'BROKEN';
    el.className = valid ? 'text-2xl font-black text-emerald-400' : 'text-2xl font-black text-rose-400';
}

export function renderGdprPending(el: HTMLElement | null, text: string): void {
    if (!el) return;
    el.classList.remove('hidden');
    applyText(el, text, 'mt-3 font-mono text-[10px] text-slate-400');
}

export function renderGdprSuccess(el: HTMLElement | null, text: string): void {
    applyText(el, text, 'mt-3 font-mono text-[10px] text-emerald-400');
}

export function renderGdprWarning(el: HTMLElement | null, text: string): void {
    applyText(el, text, 'mt-3 font-mono text-[10px] text-slate-500');
}

export function renderGdprError(el: HTMLElement | null, text: string): void {
    applyText(el, text, 'mt-3 font-mono text-[10px] text-rose-400');
}
