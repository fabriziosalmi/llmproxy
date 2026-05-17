type AuditItem = {
    req_id?: string;
    ts?: number;
    model?: string;
    status?: number;
    prompt_tokens?: number;
    completion_tokens?: number;
    cost_usd?: number;
    blocked?: boolean;
};

function el(tag: string, className?: string, text?: string): HTMLElement {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
}

export function renderAuditLoading(container: HTMLElement): void {
    container.replaceChildren(el('p', 'text-[10px] text-slate-500 font-mono', 'Loading...'));
}

export function renderAuditError(container: HTMLElement, message: string): void {
    container.replaceChildren(el('p', 'text-[10px] text-rose-400 font-mono', `Error: ${message}`));
}

export function renderAuditEmpty(container: HTMLElement, suffix = ''): void {
    container.replaceChildren(el('p', 'text-[10px] text-slate-600 font-mono', `No entries found${suffix}.`));
}

export function renderAuditTable(container: HTMLElement, items: AuditItem[], rangeLabel: string): void {
    const fragment = document.createDocumentFragment();
    fragment.appendChild(el('div', 'text-[9px] text-slate-600 font-mono mb-2', `${items.length} entries · ${rangeLabel}`));

    const table = el('table', 'w-full');
    const thead = document.createElement('thead');
    const headRow = el('tr', 'border-b border-white/[0.06]');
    ['Time', 'Model', 'Status', 'Tokens', 'Cost', 'Blocked'].forEach((label) => {
        headRow.appendChild(el('th', 'text-left text-[9px] font-bold text-slate-500 uppercase px-2 py-1', label));
    });
    thead.appendChild(headRow);
    table.appendChild(thead);

    const tbody = document.createElement('tbody');
    items.forEach((r) => {
        const row = el('tr', 'border-b border-white/[0.03] hover:bg-white/[0.02] cursor-pointer');
        const reqId = r.req_id || '';
        row.dataset.drilldown = `request:${reqId}`;
        row.tabIndex = 0;
        row.setAttribute('role', 'button');
        row.setAttribute('aria-label', `Inspect request ${reqId}`);

        const ts = r.ts ? new Date(r.ts * 1000).toLocaleString() : '--';
        const status = r.status ?? 0;
        const statusClass = status >= 400 ? 'text-rose-400' : 'text-emerald-400';
        const cost = r.cost_usd ? `$${r.cost_usd.toFixed(4)}` : '--';
        const blockedCell = el('td', 'px-2 py-1 text-[9px] font-mono');
        const blockedTag = el(
            'span',
            r.blocked ? 'text-rose-400' : 'text-emerald-400',
            r.blocked ? 'YES' : 'no'
        );
        blockedCell.appendChild(blockedTag);

        row.appendChild(el('td', 'px-2 py-1 text-[9px] font-mono text-slate-500', ts));
        row.appendChild(el('td', 'px-2 py-1 text-[10px] font-mono text-white', r.model || '--'));
        row.appendChild(el('td', `px-2 py-1 text-[10px] font-mono ${statusClass}`, String(status || '--')));
        row.appendChild(
            el(
                'td',
                'px-2 py-1 text-[9px] font-mono text-slate-400',
                `${r.prompt_tokens || 0}p+${r.completion_tokens || 0}c`
            )
        );
        row.appendChild(el('td', 'px-2 py-1 text-[9px] font-mono text-amber-400', cost));
        row.appendChild(blockedCell);
        tbody.appendChild(row);
    });
    table.appendChild(tbody);
    fragment.appendChild(table);
    container.replaceChildren(fragment);
}
