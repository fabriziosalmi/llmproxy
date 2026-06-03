import { downloadText, csvCell, stamp } from '../../../services/file_actions.js';

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

function auditCsv(items: AuditItem[]): string {
    const headers = ['req_id', 'time', 'model', 'status', 'prompt_tokens', 'completion_tokens', 'cost_usd', 'blocked'];
    const rows = items.map((r) => [
        r.req_id ?? '',
        r.ts ? new Date(r.ts * 1000).toISOString() : '',
        r.model ?? '',
        r.status ?? '',
        r.prompt_tokens ?? 0,
        r.completion_tokens ?? 0,
        r.cost_usd ?? '',
        r.blocked ? 'true' : 'false',
    ]);
    return [headers.map(csvCell).join(','), ...rows.map((row) => row.map(csvCell).join(','))].join('\n');
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
    const header = el('div', 'flex items-center justify-between gap-3 mb-2');
    header.appendChild(el('div', 'text-[9px] text-slate-600 font-mono', `${items.length} entries · ${rangeLabel}`));
    const actions = el('div', 'flex items-center gap-1');
    const csvBtn = el(
        'button',
        'text-[9px] font-bold text-slate-400 hover:text-white px-2 py-1 rounded border border-white/10 hover:bg-white/5 transition-colors',
        'Export CSV'
    ) as HTMLButtonElement;
    csvBtn.type = 'button';
    csvBtn.setAttribute('data-testid', 'audit-export-csv');
    csvBtn.addEventListener('click', () => {
        downloadText(`llmproxy-audit-${stamp()}.csv`, auditCsv(items), 'text/csv');
    });
    const jsonBtn = el(
        'button',
        'text-[9px] font-bold text-slate-400 hover:text-white px-2 py-1 rounded border border-white/10 hover:bg-white/5 transition-colors',
        'Export JSON'
    ) as HTMLButtonElement;
    jsonBtn.type = 'button';
    jsonBtn.setAttribute('data-testid', 'audit-export-json');
    jsonBtn.addEventListener('click', () => {
        downloadText(`llmproxy-audit-${stamp()}.json`, JSON.stringify({ range: rangeLabel, items }, null, 2), 'application/json');
    });
    actions.appendChild(csvBtn);
    actions.appendChild(jsonBtn);
    header.appendChild(actions);
    fragment.appendChild(header);

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
        const blockedTag = el('span', r.blocked ? 'text-rose-400' : 'text-emerald-400', r.blocked ? 'YES' : 'no');
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
