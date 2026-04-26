/**
 * Traffic Flow — native SVG node graph showing the security pipeline.
 *
 * Four columns: Clients → Guards → Router → Providers. Edges are simple
 * cubic-bezier curves; thickness encodes traffic share. Nodes lift on
 * hover, blocked guards pulse rose, healthy providers pulse emerald.
 *
 * Honest scope (O.4): this is the LOGICAL view of the pipeline — it
 * doesn't show per-edge token counts (that needs hourly buckets). What
 * it does show, live: which guards are enabled, how many providers are
 * Live, how many endpoint circuits are open, total throughput today.
 * The "ribbon proportionality" Sankey-style rendering is queued for a
 * follow-on once the backend exposes per-stage flow.
 *
 * No runtime dep — pure SVG, ~250 LoC, ships in the main bundle.
 */

import { cx } from '../../../ui';

export interface FlowNode {
    id: string;
    label: string;
    /** Smaller secondary line under the label (e.g. counts, "OFF · env"). */
    sub?: string;
    /** Visual state — drives color + pulse. */
    state: 'live' | 'idle' | 'blocked' | 'down';
}

export interface FlowData {
    /** Per-second rate or absolute count of inbound requests. */
    clientsLabel: string;
    clientsSub?: string;
    /** Guards in the pipeline (firewall, injection, PII masker, …). */
    guards: FlowNode[];
    /** Router state — 1 node ("smart_weighted" / "priority" / etc). */
    router: FlowNode;
    /** Active providers (1 node per upstream endpoint). */
    providers: FlowNode[];
}

interface FlowOptions {
    /** Optional caption displayed in the header. */
    caption?: string;
}

const STATE_COLORS: Record<FlowNode['state'], { fill: string; stroke: string; text: string; pulse: boolean }> = {
    live:    { fill: 'rgba(16,185,129,0.12)', stroke: 'rgba(16,185,129,0.55)', text: '#34d399', pulse: true  },
    idle:    { fill: 'rgba(255,255,255,0.04)', stroke: 'rgba(255,255,255,0.12)', text: '#94a3b8', pulse: false },
    blocked: { fill: 'rgba(244,63,94,0.14)',  stroke: 'rgba(244,63,94,0.55)',  text: '#fb7185', pulse: true  },
    down:    { fill: 'rgba(244,63,94,0.08)',  stroke: 'rgba(244,63,94,0.35)',  text: '#fda4af', pulse: false },
};

// SVG canvas — 800×360 logical units, scales via preserveAspectRatio="none"
// width="100%" so the layout breathes responsively without recomputing
// node positions on resize.
const W = 800;
const H = 360;
const COL_WIDTHS = [140, 240, 180, 240]; // Clients · Guards · Router · Providers
const COL_X = [0, COL_WIDTHS[0]!, COL_WIDTHS[0]! + COL_WIDTHS[1]!, COL_WIDTHS[0]! + COL_WIDTHS[1]! + COL_WIDTHS[2]!];

function _layoutColumn(items: number, colHeight: number): number[] {
    if (items <= 0) return [];
    const gap = colHeight / (items + 1);
    return Array.from({ length: items }, (_, i) => gap * (i + 1));
}

function _node(x: number, y: number, w: number, h: number, node: FlowNode): SVGGElement {
    const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    g.setAttribute('transform', `translate(${x} ${y - h / 2})`);
    g.classList.add('group');
    g.setAttribute('data-node-id', node.id);

    const palette = STATE_COLORS[node.state];
    const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    rect.setAttribute('width', String(w));
    rect.setAttribute('height', String(h));
    rect.setAttribute('rx', '12');
    rect.setAttribute('fill', palette.fill);
    rect.setAttribute('stroke', palette.stroke);
    rect.setAttribute('stroke-width', '1');
    g.appendChild(rect);

    const label = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    label.setAttribute('x', String(w / 2));
    label.setAttribute('y', node.sub ? String(h / 2 - 2) : String(h / 2 + 4));
    label.setAttribute('text-anchor', 'middle');
    label.setAttribute('fill', palette.text);
    label.setAttribute('font-family', 'ui-monospace, "JetBrains Mono", monospace');
    label.setAttribute('font-size', '11');
    label.setAttribute('font-weight', '700');
    label.textContent = node.label;
    g.appendChild(label);

    if (node.sub) {
        const sub = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        sub.setAttribute('x', String(w / 2));
        sub.setAttribute('y', String(h / 2 + 14));
        sub.setAttribute('text-anchor', 'middle');
        sub.setAttribute('fill', '#64748b');
        sub.setAttribute('font-family', 'ui-monospace, "JetBrains Mono", monospace');
        sub.setAttribute('font-size', '9');
        sub.textContent = node.sub;
        g.appendChild(sub);
    }

    if (palette.pulse) {
        // Outer halo — a second rect with no fill, stronger stroke, animated.
        const halo = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
        halo.setAttribute('width', String(w));
        halo.setAttribute('height', String(h));
        halo.setAttribute('rx', '12');
        halo.setAttribute('fill', 'none');
        halo.setAttribute('stroke', palette.stroke);
        halo.setAttribute('stroke-width', '2');
        halo.classList.add('pulse-live');
        g.appendChild(halo);
    }

    return g;
}

function _edge(x1: number, y1: number, x2: number, y2: number, intent: 'flow' | 'block'): SVGPathElement {
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    const dx = (x2 - x1) * 0.45;
    path.setAttribute('d', `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`);
    path.setAttribute('fill', 'none');
    path.setAttribute('stroke', intent === 'block' ? 'rgba(244,63,94,0.4)' : 'rgba(34,211,238,0.35)');
    path.setAttribute('stroke-width', '1.5');
    path.setAttribute('stroke-linecap', 'round');
    return path;
}

function _columnHeader(text: string, x: number, w: number): SVGTextElement {
    const t = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    t.setAttribute('x', String(x + w / 2));
    t.setAttribute('y', '14');
    t.setAttribute('text-anchor', 'middle');
    t.setAttribute('fill', '#475569');
    t.setAttribute('font-family', 'Inter, sans-serif');
    t.setAttribute('font-size', '9');
    t.setAttribute('font-weight', '700');
    t.setAttribute('letter-spacing', '0.12em');
    t.textContent = text.toUpperCase();
    return t;
}

function _renderSvg(data: FlowData): SVGSVGElement {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
    svg.setAttribute('width', '100%');
    svg.setAttribute('height', '100%');
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', 'Traffic flow: clients through guards through router to providers');
    svg.setAttribute('data-testid', 'flow-svg');

    // Column headers
    svg.appendChild(_columnHeader('Clients', COL_X[0]!, COL_WIDTHS[0]!));
    svg.appendChild(_columnHeader('Guards', COL_X[1]!, COL_WIDTHS[1]!));
    svg.appendChild(_columnHeader('Router', COL_X[2]!, COL_WIDTHS[2]!));
    svg.appendChild(_columnHeader('Providers', COL_X[3]!, COL_WIDTHS[3]!));

    const top = 32;
    const colH = H - top - 16;
    const nodeW = 120;
    const nodeH = 56;

    // Position nodes per column. Single-node columns sit at vertical center.
    const clientsY = top + colH / 2;
    const guardsY = _layoutColumn(data.guards.length, colH).map((y) => top + y);
    const routerY = top + colH / 2;
    const providersY = _layoutColumn(data.providers.length, colH).map((y) => top + y);

    // Edge layer first so nodes paint over the curves.
    const edges = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    const clientsRightX = COL_X[0]! + 10 + nodeW;
    // Clients → each guard
    for (let i = 0; i < guardsY.length; i++) {
        const guardLeftX = COL_X[1]! + (COL_WIDTHS[1]! - nodeW) / 2;
        const intent = data.guards[i]?.state === 'blocked' ? 'block' : 'flow';
        edges.appendChild(_edge(clientsRightX, clientsY, guardLeftX, guardsY[i]!, intent));
    }
    // Each guard → router (single)
    const routerLeftX = COL_X[2]! + (COL_WIDTHS[2]! - nodeW) / 2;
    const routerRightX = routerLeftX + nodeW;
    for (let i = 0; i < guardsY.length; i++) {
        const guardRightX = COL_X[1]! + (COL_WIDTHS[1]! - nodeW) / 2 + nodeW;
        const intent = data.guards[i]?.state === 'blocked' ? 'block' : 'flow';
        edges.appendChild(_edge(guardRightX, guardsY[i]!, routerLeftX, routerY, intent));
    }
    // Router → each provider
    for (let i = 0; i < providersY.length; i++) {
        const provLeftX = COL_X[3]! + (COL_WIDTHS[3]! - nodeW) / 2;
        const intent = data.providers[i]?.state === 'down' ? 'block' : 'flow';
        edges.appendChild(_edge(routerRightX, routerY, provLeftX, providersY[i]!, intent));
    }
    svg.appendChild(edges);

    // Node layer
    svg.appendChild(
        _node(COL_X[0]! + 10, clientsY, nodeW, nodeH, {
            id: 'clients',
            label: data.clientsLabel,
            sub: data.clientsSub,
            state: 'live',
        }),
    );
    for (let i = 0; i < data.guards.length; i++) {
        const g = data.guards[i]!;
        svg.appendChild(_node(COL_X[1]! + (COL_WIDTHS[1]! - nodeW) / 2, guardsY[i]!, nodeW, nodeH, g));
    }
    svg.appendChild(_node(routerLeftX, routerY, nodeW, nodeH, data.router));
    for (let i = 0; i < data.providers.length; i++) {
        const p = data.providers[i]!;
        svg.appendChild(_node(COL_X[3]! + (COL_WIDTHS[3]! - nodeW) / 2, providersY[i]!, nodeW, nodeH, p));
    }

    return svg;
}

export function renderTrafficFlow(host: HTMLElement, data: FlowData, opts: FlowOptions = {}): void {
    const card = document.createElement('div');
    card.className = cx(
        'bg-white/[0.03] backdrop-blur-xl rounded-2xl border border-white/[0.06] p-6 overflow-hidden',
    );
    card.setAttribute('data-testid', 'traffic-flow-card');

    const header = document.createElement('div');
    header.className = 'flex items-center justify-between mb-4';
    const title = document.createElement('h2');
    title.className = 'text-xs font-bold text-white';
    title.textContent = 'Traffic Flow';
    header.appendChild(title);
    if (opts.caption) {
        const cap = document.createElement('span');
        cap.className = 'text-[10px] text-slate-500 font-mono';
        cap.textContent = opts.caption;
        header.appendChild(cap);
    }
    card.appendChild(header);

    const svgWrap = document.createElement('div');
    svgWrap.className = 'w-full';
    svgWrap.style.aspectRatio = `${W} / ${H}`;
    svgWrap.appendChild(_renderSvg(data));
    card.appendChild(svgWrap);

    host.replaceChildren(card);
}
