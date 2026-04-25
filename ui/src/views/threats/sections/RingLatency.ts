import { cx } from '../../../ui';
import { RING_NAMES, RING_STYLE, type LatencyMetrics, type RingLatency, type RingName } from './types';

export function renderRingLatencyBars(container: HTMLElement, latency: LatencyMetrics): void {
    const rings = latency.rings ?? {};
    container.replaceChildren();
    container.setAttribute('data-testid', 'ring-latency-bars');

    if (RING_NAMES.every((r) => !(rings[r]?.count ?? 0))) {
        const empty = document.createElement('p');
        empty.className = 'text-[9px] text-slate-600 font-mono';
        empty.textContent = 'Collecting samples...';
        container.appendChild(empty);
        return;
    }

    const maxP99 = Math.max(1, ...RING_NAMES.map((r) => rings[r]?.p99 ?? 0));

    for (const ring of RING_NAMES) {
        const r: RingLatency = rings[ring] ?? { p50: 0, p95: 0, p99: 0, count: 0 };
        const rc = RING_STYLE[ring as RingName];

        const wrap = document.createElement('div');
        wrap.className = 'mb-2';
        wrap.setAttribute('data-testid', `ring-${ring}`);

        const head = document.createElement('div');
        head.className = 'flex items-center justify-between mb-0.5';

        const label = document.createElement('span');
        label.className = cx('text-[10px] font-bold uppercase tracking-wider', rc.text);
        label.textContent = rc.label;
        head.appendChild(label);

        const stats = document.createElement('div');
        stats.className = 'flex items-center gap-3';

        const seg = (key: string, value: number, valueClass: string): HTMLElement => {
            const s = document.createElement('span');
            s.className = 'text-[10px] font-mono text-slate-500';
            s.append(`${key} `);
            const colored = document.createElement('span');
            colored.className = valueClass;
            colored.textContent = `${value.toFixed(1)}ms`;
            s.appendChild(colored);
            return s;
        };
        stats.appendChild(seg('P50', r.p50 ?? 0, 'text-white'));
        stats.appendChild(seg('P95', r.p95 ?? 0, 'text-amber-400'));
        stats.appendChild(seg('P99', r.p99 ?? 0, 'text-rose-400'));
        const countSpan = document.createElement('span');
        countSpan.className = 'text-[9px] font-mono text-slate-600';
        countSpan.textContent = `${r.count ?? 0}x`;
        stats.appendChild(countSpan);
        head.appendChild(stats);

        wrap.appendChild(head);

        const track = document.createElement('div');
        track.className = 'w-full h-1.5 bg-white/5 rounded-full overflow-hidden';
        const fill = document.createElement('div');
        const barWidth = maxP99 > 0 ? Math.max(2, ((r.p99 ?? 0) / maxP99) * 100) : 0;
        fill.className = cx('h-full rounded-full transition-all', rc.bar);
        fill.style.width = `${barWidth}%`;
        track.appendChild(fill);
        wrap.appendChild(track);

        container.appendChild(wrap);
    }
}

export function renderTtft(container: HTMLElement, ttft: LatencyMetrics['ttft']): void {
    container.replaceChildren();
    container.setAttribute('data-testid', 'ttft-metrics');

    if (!ttft || (ttft.samples ?? 0) === 0) {
        const empty = document.createElement('p');
        empty.className = 'text-[9px] text-slate-600 font-mono';
        empty.textContent = 'No streaming data yet';
        container.appendChild(empty);
        return;
    }

    const p50 = ttft.p50 ?? 0;
    const p95 = ttft.p95 ?? 0;
    const p99 = ttft.p99 ?? 0;
    const colorBar = p95 > 1000 ? 'bg-rose-500/40' : p95 > 500 ? 'bg-amber-500/40' : 'bg-emerald-500/40';
    const colorText = p95 > 1000 ? 'text-rose-400' : p95 > 500 ? 'text-amber-400' : 'text-emerald-400';

    const top = document.createElement('div');
    top.className = 'flex items-center gap-6 mb-3';
    const tile = (value: string, label: string, valueClass = 'text-white', size = 'text-2xl'): HTMLElement => {
        const box = document.createElement('div');
        const v = document.createElement('span');
        v.className = `${size} font-black font-mono ${valueClass}`;
        v.textContent = value;
        box.appendChild(v);
        const l = document.createElement('span');
        l.className = 'text-[10px] text-slate-500 ml-1';
        l.textContent = label;
        box.appendChild(l);
        return box;
    };
    top.appendChild(tile(p50.toFixed(0), 'ms P50'));
    top.appendChild(tile(p95.toFixed(0), 'ms P95', colorText, 'text-lg'));
    top.appendChild(tile(p99.toFixed(0), 'ms P99', 'text-rose-400', 'text-lg'));
    container.appendChild(top);

    const bottom = document.createElement('div');
    bottom.className = 'flex items-center gap-2';
    const samples = document.createElement('span');
    samples.className = 'text-[10px] font-mono text-slate-600';
    samples.textContent = `${ttft.samples ?? 0} stream samples`;
    bottom.appendChild(samples);

    const track = document.createElement('div');
    track.className = 'flex-1 h-1 bg-white/5 rounded-full overflow-hidden';
    const fill = document.createElement('div');
    fill.className = cx('h-full rounded-full', colorBar);
    fill.style.width = `${Math.min(100, (p50 / 2000) * 100)}%`;
    track.appendChild(fill);
    bottom.appendChild(track);

    const target = document.createElement('span');
    target.className = 'text-[10px] font-mono text-slate-600';
    target.textContent = '2s target';
    bottom.appendChild(target);

    container.appendChild(bottom);
}
