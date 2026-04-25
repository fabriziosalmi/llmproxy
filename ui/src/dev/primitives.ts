/**
 * Storybook-lite — bootstraps the gallery defined in `dev/primitives.html`.
 *
 * Loaded ONLY when the page is served by Vite (dev mode). The page is not
 * registered in `rollupOptions.input`, so it does not ship in `dist/`.
 */
import '../../style.css';
import { stories, type Story } from './stories';

interface CodeishLabel {
    text: string;
    mono?: boolean;
}

function group(stories: Story[]): Map<string, Story[]> {
    const groups = new Map<string, Story[]>();
    for (const s of stories) {
        const list = groups.get(s.primitive) ?? [];
        list.push(s);
        groups.set(s.primitive, list);
    }
    return groups;
}

function makeLabel({ text, mono }: CodeishLabel): HTMLElement {
    const el = document.createElement('p');
    el.className = mono ? 'text-[10px] font-mono text-slate-500' : 'text-[11px] font-semibold text-slate-300';
    el.textContent = text;
    return el;
}

function makeVariantCell(s: Story): HTMLElement {
    const cell = document.createElement('article');
    cell.className = 'flex flex-col gap-3 p-4 rounded-xl border border-white/[0.06] bg-white/[0.02]';

    cell.appendChild(makeLabel({ text: s.variant, mono: true }));

    const stage = document.createElement('div');
    stage.className = 'flex items-start justify-start min-h-[3rem]';
    stage.appendChild(s.render());
    cell.appendChild(stage);

    if (s.description) {
        const desc = document.createElement('p');
        desc.className = 'text-[10px] text-slate-500 leading-relaxed';
        desc.textContent = s.description;
        cell.appendChild(desc);
    }
    return cell;
}

function makeSection(name: string, items: Story[]): HTMLElement {
    const section = document.createElement('section');
    section.id = `prim-${name.toLowerCase()}`;
    section.className = 'flex flex-col gap-4';

    const head = document.createElement('header');
    head.className = 'flex items-baseline justify-between border-b border-white/[0.04] pb-2';
    const h2 = document.createElement('h2');
    h2.className = 'text-sm font-bold text-white tracking-tight';
    h2.textContent = name;
    const count = document.createElement('span');
    count.className = 'text-[10px] font-mono text-slate-500';
    count.textContent = `${items.length} variant${items.length === 1 ? '' : 's'}`;
    head.appendChild(h2);
    head.appendChild(count);
    section.appendChild(head);

    const grid = document.createElement('div');
    grid.className = 'grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3';
    for (const s of items) grid.appendChild(makeVariantCell(s));
    section.appendChild(grid);

    return section;
}

function makeNav(groups: Map<string, Story[]>): HTMLElement {
    const nav = document.createElement('nav');
    nav.className = 'flex flex-wrap gap-2 mb-8';
    for (const [name, items] of groups) {
        const link = document.createElement('a');
        link.href = `#prim-${name.toLowerCase()}`;
        link.className =
            'inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border border-white/[0.06] text-[11px] font-mono text-slate-300 hover:bg-white/[0.05] hover:text-white transition-colors';
        link.textContent = name;
        const count = document.createElement('span');
        count.className = 'text-[9px] text-slate-500';
        count.textContent = String(items.length);
        link.appendChild(count);
        nav.appendChild(link);
    }
    return nav;
}

function mount(): void {
    const root = document.getElementById('gallery');
    if (!root) {
        console.error('#gallery not found');
        return;
    }
    const groups = group(stories);

    root.appendChild(makeNav(groups));
    for (const [name, items] of groups) root.appendChild(makeSection(name, items));
}

document.addEventListener('DOMContentLoaded', mount);
