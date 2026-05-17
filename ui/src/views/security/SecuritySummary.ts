type CorpusStats = {
    total_patterns?: number;
    categories?: Record<string, number>;
};

function el(tag: string, className?: string, text?: string): HTMLElement {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
}

export function renderTrackedIps(elm: HTMLElement | null, value: number | string): void {
    if (!elm) return;
    elm.textContent = String(value ?? '—');
}

export function renderSigningStatus(elm: HTMLElement | null, enabled: boolean): void {
    if (!elm) return;
    elm.textContent = enabled ? 'ACTIVE' : 'OFF';
    elm.className = enabled ? 'text-2xl font-black text-emerald-400' : 'text-2xl font-black text-slate-500';
}

export function renderRetentionInfo(elm: HTMLElement | null, text: string): void {
    if (!elm) return;
    elm.textContent = text;
}

export function renderCorpus(countElm: HTMLElement | null, categoriesElm: HTMLElement | null, stats: CorpusStats): void {
    if (countElm) countElm.textContent = String(stats.total_patterns ?? 0);
    if (!categoriesElm) return;
    const categories = stats.categories || {};
    const fragment = document.createDocumentFragment();
    Object.entries(categories).forEach(([cat, count]) => {
        const wrap = el('div', 'bg-white/5 rounded-lg p-2 text-center');
        wrap.appendChild(el('p', 'text-sm font-bold text-white', String(count)));
        wrap.appendChild(el('p', 'text-[9px] text-slate-500 uppercase', cat));
        fragment.appendChild(wrap);
    });
    categoriesElm.replaceChildren(fragment);
}
