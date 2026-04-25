interface EndpointStats {
    requests: number;
    errors: number;
}

/** Pure parser exported for tests. Aggregates per-endpoint counters from a Prometheus text exposition. */
export function parseEndpointBreakdown(promText: string): Record<string, EndpointStats> {
    const map: Record<string, EndpointStats> = {};
    for (const line of promText.split('\n')) {
        if (!line || line.startsWith('#')) continue;
        let m = line.match(/llm_proxy_requests_total\{[^}]*endpoint="([^"]+)"[^}]*\}\s+([\d.]+)/);
        if (m) {
            const ep = m[1]!;
            const val = Number.parseFloat(m[2]!);
            if (!Number.isNaN(val)) {
                if (!map[ep]) map[ep] = { requests: 0, errors: 0 };
                map[ep].requests += val;
            }
            continue;
        }
        m = line.match(/llm_proxy_request_errors_total\{[^}]*endpoint="([^"]+)"[^}]*\}\s+([\d.]+)/);
        if (m) {
            const ep = m[1]!;
            const val = Number.parseFloat(m[2]!);
            if (!Number.isNaN(val)) {
                if (!map[ep]) map[ep] = { requests: 0, errors: 0 };
                map[ep].errors += val;
            }
        }
    }
    return map;
}

export function renderEndpointBreakdown(container: HTMLElement, promText: string): void {
    const map = parseEndpointBreakdown(promText);
    const entries = Object.entries(map);

    container.replaceChildren();
    container.dataset.ready = '1';
    container.setAttribute('data-testid', 'endpoint-breakdown');

    if (entries.length === 0) {
        const empty = document.createElement('p');
        empty.className = 'text-[9px] text-slate-600 font-mono';
        empty.textContent = 'No per-endpoint data yet';
        container.appendChild(empty);
        return;
    }

    for (const [ep, data] of entries) {
        const row = document.createElement('div');
        row.className = 'flex items-center justify-between py-1.5 border-b border-white/[0.04] last:border-0';

        const epSpan = document.createElement('span');
        epSpan.className = 'text-[9px] font-mono text-slate-400 truncate max-w-[200px]';
        epSpan.textContent = ep;
        row.appendChild(epSpan);

        const right = document.createElement('div');
        right.className = 'flex items-center gap-4';

        const reqSpan = document.createElement('span');
        reqSpan.className = 'text-[9px] font-mono text-slate-500';
        reqSpan.textContent = `${data.requests.toLocaleString()} req`;
        right.appendChild(reqSpan);

        const errRate = data.requests > 0 ? (data.errors / data.requests) * 100 : 0;
        const errColor = errRate > 5 ? 'text-rose-400' : errRate > 0 ? 'text-amber-400' : 'text-emerald-400';
        const errSpan = document.createElement('span');
        errSpan.className = `text-[9px] font-mono ${errColor}`;
        errSpan.textContent = `${errRate.toFixed(1)}% err`;
        right.appendChild(errSpan);

        row.appendChild(right);
        container.appendChild(row);
    }
}
