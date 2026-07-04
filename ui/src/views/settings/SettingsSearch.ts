/**
 * Global settings search — "find any setting instantly".
 *
 * Searches the Guided Configuration schema (every knob's label / help / path)
 * across all its sections and, on select, jumps to the Configuration tab and
 * highlights the field. This is what makes a tabbed Settings navigable without
 * knowing which tab a given knob lives in.
 */
import { CONFIG_SECTIONS, ALL_FIELDS, type ConfigField } from './schema/configSchema';

export interface SettingsSearchOptions {
    /** Jump to and highlight a config field by dotted path. */
    openField: (path: string) => void;
    /** Injectable field set (tests); defaults to the full schema. */
    fields?: ConfigField[];
}

export interface SettingsSearchHandle {
    root: HTMLElement;
    focus: () => void;
}

const SECTION_OF: Record<string, string> = (() => {
    const m: Record<string, string> = {};
    for (const s of CONFIG_SECTIONS) for (const f of s.fields) m[f.path] = s.title;
    return m;
})();

interface Ranked {
    field: ConfigField;
    score: number;
}

/** Rank fields for a query: label hit ranks above help/section, prefix above substring. */
export function searchFields(query: string, fields: ConfigField[] = ALL_FIELDS, limit = 8): ConfigField[] {
    const q = query.trim().toLowerCase();
    if (!q) return [];
    const terms = q.split(/\s+/);
    const ranked: Ranked[] = [];
    for (const field of fields) {
        const label = field.label.toLowerCase();
        const help = field.help.toLowerCase();
        const section = (SECTION_OF[field.path] || '').toLowerCase();
        const path = field.path.toLowerCase();
        let score = 0;
        let allMatched = true;
        for (const t of terms) {
            if (label.startsWith(t)) score += 100;
            else if (label.includes(t)) score += 60;
            else if (path.includes(t)) score += 40;
            else if (section.includes(t)) score += 25;
            else if (help.includes(t)) score += 15;
            else {
                allMatched = false;
                break;
            }
        }
        if (allMatched) ranked.push({ field, score });
    }
    ranked.sort((a, b) => b.score - a.score || a.field.label.localeCompare(b.field.label));
    return ranked.slice(0, limit).map((r) => r.field);
}

export function sectionTitleOf(path: string): string {
    return SECTION_OF[path] || '';
}

export function mountSettingsSearch(host: HTMLElement, opts: SettingsSearchOptions): SettingsSearchHandle {
    const fields = opts.fields ?? ALL_FIELDS;

    const root = document.createElement('div');
    root.className = 'relative';
    root.setAttribute('data-testid', 'settings-search');

    const inputWrap = document.createElement('div');
    inputWrap.className =
        'flex items-center gap-2 bg-white/5 border border-white/10 rounded-lg px-3 py-2 ' +
        'focus-within:border-cyan-500/50';
    const icon = document.createElement('span');
    icon.className = 'text-slate-500 text-xs';
    icon.textContent = '⌕';
    icon.setAttribute('aria-hidden', 'true');
    const input = document.createElement('input');
    input.type = 'search';
    input.placeholder = 'Search settings… (e.g. homograph, cache TTL, rate limit)';
    input.className = 'flex-1 bg-transparent text-[12px] text-white placeholder:text-slate-600 focus:outline-none';
    input.setAttribute('role', 'combobox');
    input.setAttribute('aria-expanded', 'false');
    input.setAttribute('aria-controls', 'settings-search-results');
    input.setAttribute('aria-autocomplete', 'list');
    input.setAttribute('data-testid', 'settings-search-input');
    inputWrap.append(icon, input);
    root.appendChild(inputWrap);

    const results = document.createElement('ul');
    results.id = 'settings-search-results';
    results.setAttribute('role', 'listbox');
    results.className =
        'absolute left-0 right-0 top-full mt-1 z-30 hidden max-h-80 overflow-y-auto ' +
        'bg-[#0c0c10] border border-white/10 rounded-lg shadow-2xl divide-y divide-white/[0.04]';
    root.appendChild(results);

    host.replaceChildren(root);

    let matches: ConfigField[] = [];
    let cursor = -1;

    function close(): void {
        results.classList.add('hidden');
        input.setAttribute('aria-expanded', 'false');
        cursor = -1;
    }

    function choose(field: ConfigField): void {
        opts.openField(field.path);
        input.value = '';
        matches = [];
        close();
    }

    function paintCursor(): void {
        Array.from(results.children).forEach((li, i) => {
            const el = li as HTMLElement;
            const active = i === cursor;
            el.classList.toggle('bg-white/[0.06]', active);
            el.setAttribute('aria-selected', String(active));
            if (active) {
                el.scrollIntoView({ block: 'nearest' });
                input.setAttribute('aria-activedescendant', el.id);
            }
        });
    }

    function render(): void {
        results.replaceChildren();
        if (!matches.length) {
            close();
            return;
        }
        matches.forEach((field, i) => {
            const li = document.createElement('li');
            li.setAttribute('role', 'option');
            li.id = `settings-search-opt-${i}`;
            li.className = 'px-3 py-2 cursor-pointer hover:bg-white/[0.06]';
            li.setAttribute('data-testid', `settings-search-opt-${field.path}`);

            const top = document.createElement('div');
            top.className = 'flex items-center justify-between gap-2';
            const name = document.createElement('span');
            name.className = 'text-[11px] font-semibold text-white';
            name.textContent = field.label;
            const sec = document.createElement('span');
            sec.className = 'text-[9px] font-mono text-slate-500 shrink-0';
            sec.textContent = sectionTitleOf(field.path);
            top.append(name, sec);

            const help = document.createElement('p');
            help.className = 'text-[10px] text-slate-500 truncate';
            help.textContent = field.help;

            li.append(top, help);
            li.addEventListener('mousedown', (e) => {
                e.preventDefault(); // keep focus; fire before blur
                choose(field);
            });
            li.addEventListener('mousemove', () => {
                cursor = i;
                paintCursor();
            });
            results.appendChild(li);
        });
        results.classList.remove('hidden');
        input.setAttribute('aria-expanded', 'true');
        cursor = 0;
        paintCursor();
    }

    input.addEventListener('input', () => {
        matches = searchFields(input.value, fields);
        render();
    });

    input.addEventListener('keydown', (e) => {
        if (results.classList.contains('hidden')) return;
        if (e.key === 'ArrowDown') {
            e.preventDefault();
            cursor = (cursor + 1) % matches.length;
            paintCursor();
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            cursor = (cursor - 1 + matches.length) % matches.length;
            paintCursor();
        } else if (e.key === 'Enter') {
            e.preventDefault();
            if (matches[cursor]) choose(matches[cursor]);
        } else if (e.key === 'Escape') {
            input.value = '';
            matches = [];
            close();
        }
    });

    input.addEventListener('blur', () => {
        // Delay so a click/mousedown on a result still registers.
        setTimeout(close, 120);
    });

    return { root, focus: () => input.focus() };
}
