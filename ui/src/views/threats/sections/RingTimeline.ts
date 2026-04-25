import { cx } from '../../../ui';
import { RING_NAMES, RING_STYLE, type RingTrace } from './types';

export function renderRingTimeline(container: HTMLElement, traces: RingTrace[]): void {
    container.replaceChildren();
    container.setAttribute('data-testid', 'ring-timeline');

    if (!traces.length) {
        const empty = document.createElement('p');
        empty.className = 'text-[9px] text-slate-600 font-mono';
        empty.textContent = 'No request traces yet';
        container.appendChild(empty);
        return;
    }

    for (const trace of traces) {
        const rings = trace.rings ?? {};
        const total = trace.total_ms ?? 0;
        const upstream = trace.upstream_ms ?? 0;
        const ringDurations = Object.values(rings).reduce((s, r) => s + (r?.duration_ms ?? 0), 0);
        const maxMs = Math.max(1, total || ringDurations + upstream);

        const row = document.createElement('div');
        row.className = 'flex items-center gap-3 group';
        if (trace.req_id) row.setAttribute('data-req-id', trace.req_id);

        const ts = document.createElement('span');
        ts.className = 'text-[10px] font-mono text-slate-600 w-16 shrink-0';
        ts.textContent = trace.timestamp ? new Date(trace.timestamp * 1000).toLocaleTimeString() : '--';
        row.appendChild(ts);

        const reqId = document.createElement('span');
        reqId.className = 'text-[9px] font-mono text-slate-500 w-12 shrink-0';
        reqId.textContent = trace.req_id ?? '--';
        row.appendChild(reqId);

        const track = document.createElement('div');
        track.className = 'flex-1 h-3 bg-white/[0.03] rounded-full overflow-hidden flex';
        for (const ring of RING_NAMES) {
            const r = rings[ring];
            if (!r) continue;
            const seg = document.createElement('div');
            const width = Math.max(1, ((r.duration_ms ?? 0) / maxMs) * 100);
            seg.className = cx('h-full rounded-sm', RING_STYLE[ring].bar);
            seg.style.width = `${width}%`;
            const plugins = (r.plugins ?? []).map((p) => `${p.name}: ${p.ms}ms`).join(', ');
            seg.title = `${RING_STYLE[ring].label}: ${r.duration_ms}ms${plugins ? `\n${plugins}` : ''}`;
            track.appendChild(seg);
        }
        if (upstream > 0) {
            const upstreamSeg = document.createElement('div');
            upstreamSeg.className = 'bg-emerald-500/60 h-full rounded-sm';
            upstreamSeg.style.width = `${Math.max(1, (upstream / maxMs) * 100)}%`;
            upstreamSeg.title = `Upstream: ${upstream}ms`;
            track.appendChild(upstreamSeg);
        }
        row.appendChild(track);

        const totalSpan = document.createElement('span');
        totalSpan.className = 'text-[10px] font-mono text-white w-16 text-right shrink-0';
        totalSpan.textContent = `${total.toFixed(0)}ms`;
        row.appendChild(totalSpan);

        if (trace.ttft_ms) {
            const ttftBadge = document.createElement('span');
            ttftBadge.className = 'text-[9px] font-mono text-sky-400 bg-sky-500/10 px-1 py-0.5 rounded';
            ttftBadge.textContent = `TTFT ${trace.ttft_ms}ms`;
            row.appendChild(ttftBadge);
        }

        container.appendChild(row);
    }
}
