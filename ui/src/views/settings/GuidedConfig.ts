/**
 * Settings → Guided Configuration (admin-only).
 *
 * A field-by-field, self-documenting editor for the scalar config.yaml knobs
 * (see schema/configSchema). Each control shows an inline "what does this do?"
 * so an operator who doesn't live in the codebase can change settings safely.
 *
 * Writes go through the same hardened backend as the raw editor:
 *   GET /config/raw → edit only the changed leaves on the source text (surgical,
 *   comment-preserving) → POST /config/validate → confirm → POST /config/apply
 *   (atomic write + timestamped backup + hot-reload + auto-rollback on failure).
 *
 * The raw YAML editor remains below as the "Advanced" surface for structural
 * sections (endpoints, model_groups, …) that this guided form intentionally
 * does not cover.
 */
import {
    confirm,
    createButton,
    createErrorState,
    createInput,
    createSelect,
    createSkeleton,
    createToggle,
} from '../../ui';
import { ALL_FIELDS, CONFIG_SECTIONS, fieldError, type ConfigField } from './schema/configSchema';
import { getScalar, setScalar, type Scalar } from './schema/yamlEdit';

export interface GuidedConfigApi {
    fetchConfigRaw: () => Promise<{ yaml: string; path?: string }>;
    validateConfig: (yaml: string) => Promise<{ valid: boolean; errors: string[]; warnings: string[] }>;
    applyConfig: (yaml: string) => Promise<{ applied: boolean; warnings?: string[]; backup?: string }>;
}

export interface GuidedConfigHandle {
    refresh: () => Promise<void>;
    /** Reveal (un-hiding advanced if needed), scroll to, highlight and focus a field. */
    focusPath: (path: string) => void;
    /** Number of fields edited but not yet applied. */
    dirtyCount: () => number;
    /** True when there are unsaved edits. */
    isDirty: () => boolean;
}

export interface GuidedConfigOptions {
    /** Fired whenever the unsaved-change count changes (for tab badges, guards). */
    onDirty?: (count: number) => void;
}

type Toast = (m: string, k?: 'success' | 'error' | 'warning' | 'info') => void;

function parseApplyError(err: unknown): { errors: string[]; warnings: string[] } {
    try {
        const e = err as { body?: string; message?: string };
        const body = JSON.parse(e?.body ?? '{}');
        const detail = body?.detail ?? {};
        if (Array.isArray(detail?.errors)) return { errors: detail.errors, warnings: detail.warnings ?? [] };
        if (typeof detail === 'string') return { errors: [detail], warnings: [] };
    } catch {
        /* fall through */
    }
    return { errors: [(err as { message?: string })?.message || 'Apply failed'], warnings: [] };
}

/** A rendered field control with dirty-tracking against its on-disk value. */
interface Control {
    field: ConfigField;
    root: HTMLElement;
    getValue: () => Scalar;
    setError: (msg: string | null) => void;
    isDirty: () => boolean;
}

function sameValue(a: Scalar, b: Scalar): boolean {
    if (Array.isArray(a) || Array.isArray(b)) return JSON.stringify(a) === JSON.stringify(b);
    return a === b;
}

/** A DOM-/CSS-safe element id for a dotted config path (no `.` in ids). */
function domId(path: string): string {
    return `guided-${path.replace(/\./g, '-')}`;
}

/** Inline chip editor for a string list (blocked_domains, brands). */
function createChips(field: ConfigField, initial: string[], onChange: () => void): Control {
    let items = [...initial];
    const root = document.createElement('div');
    root.className = 'flex flex-col gap-1';
    root.setAttribute('data-testid', domId(field.path));

    const label = document.createElement('label');
    label.className = 'text-[9px] font-bold text-slate-500 uppercase tracking-widest';
    label.textContent = field.label;
    root.appendChild(label);

    const box = document.createElement('div');
    box.className =
        'flex flex-wrap gap-1.5 items-center bg-white/5 border border-white/10 rounded-lg px-2 py-1.5 min-h-[34px]';
    const input = document.createElement('input');
    input.type = 'text';
    input.placeholder = field.placeholder || 'add…';
    input.className = 'flex-1 min-w-[80px] bg-transparent text-[11px] text-white font-mono focus:outline-none';
    input.setAttribute('data-testid', `${domId(field.path)}-input`);

    function repaint(): void {
        box.replaceChildren();
        for (const it of items) {
            const chip = document.createElement('span');
            chip.className =
                'inline-flex items-center gap-1 text-[10px] font-mono text-cyan-200 bg-cyan-500/10 ' +
                'border border-cyan-500/20 rounded px-1.5 py-0.5';
            const t = document.createElement('span');
            t.textContent = it;
            chip.appendChild(t);
            const x = document.createElement('button');
            x.type = 'button';
            x.textContent = '×';
            x.className = 'text-cyan-300/70 hover:text-white leading-none';
            x.setAttribute('aria-label', `Remove ${it}`);
            x.addEventListener('click', () => {
                items = items.filter((v) => v !== it);
                repaint();
                onChange();
            });
            chip.appendChild(x);
            box.appendChild(chip);
        }
        box.appendChild(input);
    }

    function commit(): void {
        const raw = input.value.trim().replace(/,+$/, '').trim();
        if (raw && !items.includes(raw)) {
            items.push(raw);
            onChange();
        }
        input.value = '';
        repaint();
    }
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ',') {
            e.preventDefault();
            commit();
        } else if (e.key === 'Backspace' && input.value === '' && items.length) {
            items.pop();
            repaint();
            onChange();
        }
    });
    input.addEventListener('blur', commit);
    root.appendChild(box);

    const help = document.createElement('p');
    help.className = 'text-[10px] text-slate-500';
    help.textContent = field.help;
    root.appendChild(help);

    repaint();
    return {
        field,
        root,
        getValue: () => [...items],
        setError: (msg) => {
            box.classList.toggle('border-rose-500/60', !!msg);
        },
        isDirty: () => !sameValue(items, initial),
    };
}

function buildControl(field: ConfigField, current: Scalar, onChange: () => void): Control {
    const restartNote = field.restartRequired ? ' — applies only after a restart.' : '';
    const help = field.help + restartNote;

    if (field.type === 'boolean') {
        const initial = typeof current === 'boolean' ? current : !!field.default;
        const t = createToggle({
            label: field.label,
            checked: initial,
            description: help,
            onChange,
            testId: domId(field.path),
        });
        t.root.classList.add('py-1');
        return {
            field,
            root: t.root,
            getValue: () => t.isChecked(),
            setError: () => {},
            isDirty: () => t.isChecked() !== initial,
        };
    }

    if (field.type === 'enum') {
        const initial = typeof current === 'string' ? current : String(field.default ?? '');
        const s = createSelect({
            name: domId(field.path),
            label: field.label,
            options: field.options ?? [],
            value: initial,
            helpText: help,
            onChange,
            testId: domId(field.path),
        });
        return {
            field,
            root: s.root,
            getValue: () => s.getValue(),
            setError: (m) => s.setError(m),
            isDirty: () => s.getValue() !== initial,
        };
    }

    if (field.type === 'stringList') {
        const initial = Array.isArray(current) ? (current as string[]) : ((field.default as string[]) ?? []);
        return createChips(field, initial, onChange);
    }

    // number | string
    const isNum = field.type === 'number';
    const initialStr =
        current != null && current !== '' ? String(current) : field.default != null ? String(field.default) : '';
    const i = createInput({
        name: domId(field.path),
        label: field.label,
        type: isNum ? 'number' : 'text',
        value: initialStr,
        helpText: help,
        placeholder: field.placeholder,
        onInput: onChange,
        testId: domId(field.path),
    });
    return {
        field,
        root: i.root,
        getValue: () => (isNum ? Number(i.getValue()) : i.getValue()),
        setError: (m) => i.setError(m),
        isDirty: () => i.getValue().trim() !== initialStr.trim(),
    };
}

export function mountGuidedConfig(
    host: HTMLElement,
    api: GuidedConfigApi,
    toast?: Toast,
    guidedOpts: GuidedConfigOptions = {}
): GuidedConfigHandle {
    const card = document.createElement('div');
    card.className = 'bg-white/[0.03] backdrop-blur-xl rounded-2xl border border-white/[0.06] p-6';
    card.setAttribute('data-testid', 'settings-guided-config');

    const head = document.createElement('div');
    head.className = 'flex items-center justify-between mb-1';
    const title = document.createElement('h2');
    title.className = 'text-xs font-bold text-white';
    title.textContent = 'Guided Configuration';
    head.appendChild(title);
    const note = document.createElement('span');
    note.className = 'text-[10px] text-slate-500 font-mono';
    note.textContent = 'admin · validated · backed up before apply';
    head.appendChild(note);
    card.appendChild(head);

    const sub = document.createElement('p');
    sub.className = 'text-[10px] text-slate-500 mb-3';
    sub.textContent =
        'Change common settings with plain-language help. Structural sections stay in the Advanced editor below.';
    card.appendChild(sub);

    const body = document.createElement('div');
    card.appendChild(body);
    host.replaceChildren(card);

    let controls: Control[] = [];
    let showAdvanced = false;

    const result = document.createElement('div');
    result.className = 'mt-3 font-mono text-[10px] min-h-[1rem]';

    function renderResult(kind: 'ok' | 'error' | 'warning' | 'info', lines: string[]): void {
        const tone =
            kind === 'ok'
                ? 'text-emerald-400'
                : kind === 'error'
                  ? 'text-rose-400'
                  : kind === 'warning'
                    ? 'text-amber-400'
                    : 'text-slate-400';
        result.className = `mt-3 font-mono text-[10px] min-h-[1rem] ${tone}`;
        result.replaceChildren();
        for (const line of lines) {
            const p = document.createElement('div');
            p.textContent = line;
            result.appendChild(p);
        }
    }

    const dirtyBadge = document.createElement('span');
    dirtyBadge.className = 'text-[10px] font-mono text-amber-400';

    const saveBtn = createButton({
        label: 'Review & apply',
        variant: 'primary',
        size: 'sm',
        testId: 'guided-save-btn',
        onClick: () => void onSave(),
    });
    const resetBtn = createButton({
        label: 'Reset',
        variant: 'ghost',
        size: 'sm',
        testId: 'guided-reset-btn',
        onClick: () => void refresh(),
    });

    function dirtyControls(): Control[] {
        return controls.filter((c) => c.isDirty());
    }

    function recompute(): void {
        const n = dirtyControls().length;
        dirtyBadge.textContent = n ? `${n} unsaved change${n === 1 ? '' : 's'}` : 'No changes';
        (saveBtn as HTMLButtonElement).disabled = n === 0;
        guidedOpts.onDirty?.(n);
    }

    let originalYaml = '';

    async function onSave(): Promise<void> {
        const dirty = dirtyControls();
        if (!dirty.length) return;

        // 1. Client-side field validation.
        let firstBad: Control | null = null;
        for (const c of dirty) {
            const err = fieldError(c.field, c.getValue());
            c.setError(err);
            if (err && !firstBad) firstBad = c;
        }
        if (firstBad) {
            renderResult('error', ['Fix the highlighted field(s) before applying.']);
            firstBad.root.scrollIntoView({ block: 'center', behavior: 'smooth' });
            return;
        }

        // 2. Build the new YAML by editing only the changed leaves.
        let next = originalYaml;
        for (const c of dirty) next = setScalar(next, c.field.path, c.getValue());

        // 3. Server dry-run.
        renderResult('info', ['Validating…']);
        let pre;
        try {
            pre = await api.validateConfig(next);
        } catch (e) {
            renderResult('error', [`✗ ${(e as { message?: string })?.message || 'Validation request failed'}`]);
            return;
        }
        if (!pre.valid) {
            renderResult(
                'error',
                (pre.errors || ['Invalid config']).map((x) => `✗ ${x}`)
            );
            return;
        }

        // 4. Confirm + apply.
        const changedPaths = dirty.map((c) => c.field.path);
        const needsRestart = dirty.some((c) => c.field.restartRequired);
        const ok = await confirm({
            title: 'Apply configuration',
            message:
                `This writes ${changedPaths.length} change${changedPaths.length === 1 ? '' : 's'} to config.yaml and ` +
                `hot-reloads the proxy. The current file is backed up first and rolled back automatically if the ` +
                `new one fails to load.` +
                (needsRestart ? ' Note: one or more changes only take effect after a full restart.' : '') +
                ' Proceed?',
            confirmLabel: 'Write & reload',
            danger: true,
        });
        if (!ok) return;

        renderResult('info', ['Applying…']);
        try {
            const res = await api.applyConfig(next);
            renderResult('ok', [
                `✓ Applied ${changedPaths.length} change${changedPaths.length === 1 ? '' : 's'}${res.backup ? ` (backup: ${res.backup})` : ''}.`,
                ...(needsRestart ? ['⚠ A restart is required for some changes to take effect.'] : []),
                ...(res.warnings || []).map((w) => `⚠ ${w}`),
            ]);
            toast?.('Configuration applied and reloaded', 'success');
            await refresh();
        } catch (e) {
            const { errors, warnings } = parseApplyError(e);
            renderResult('error', [...errors.map((x) => `✗ ${x}`), ...warnings.map((w) => `⚠ ${w}`)]);
            toast?.('Config apply failed — nothing changed', 'error');
        }
    }

    function renderForm(): void {
        controls = [];
        const frag = document.createDocumentFragment();

        // Advanced toggle
        const advRow = document.createElement('div');
        advRow.className = 'flex justify-end mb-2';
        const advToggle = createToggle({
            label: 'Show advanced',
            checked: showAdvanced,
            onChange: (v) => {
                showAdvanced = v;
                renderForm();
            },
            testId: 'guided-advanced-toggle',
        });
        advRow.appendChild(advToggle.root);
        frag.appendChild(advRow);

        for (const section of CONFIG_SECTIONS) {
            const visible = section.fields.filter((f) => showAdvanced || !f.advanced);
            if (!visible.length) continue;

            const secEl = document.createElement('div');
            secEl.className = 'mb-5';
            const h = document.createElement('h3');
            h.className = 'text-[11px] font-bold text-slate-300 mb-0.5';
            h.textContent = section.title;
            secEl.appendChild(h);
            const d = document.createElement('p');
            d.className = 'text-[10px] text-slate-500 mb-2.5';
            d.textContent = section.description;
            secEl.appendChild(d);

            const grid = document.createElement('div');
            grid.className = 'grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-3';
            for (const field of visible) {
                const cur = getScalar(originalYaml, field.path);
                const control = buildControl(field, (cur ?? field.default) as Scalar, recompute);
                controls.push(control);
                grid.appendChild(control.root);
            }
            secEl.appendChild(grid);
            frag.appendChild(secEl);
        }

        // Footer action bar
        const footer = document.createElement('div');
        footer.className = 'flex items-center gap-3 mt-4 pt-3 border-t border-white/[0.06]';
        footer.append(saveBtn, resetBtn, dirtyBadge);
        frag.appendChild(footer);
        frag.appendChild(result);

        body.replaceChildren(frag);
        recompute();
    }

    async function refresh(): Promise<void> {
        body.replaceChildren(createSkeleton({ repeat: 8 }));
        try {
            const { yaml } = await api.fetchConfigRaw();
            originalYaml = yaml || '';
            result.replaceChildren();
            result.className = 'mt-3 font-mono text-[10px] min-h-[1rem]';
            renderForm();
        } catch (e) {
            body.replaceChildren(
                createErrorState({
                    title: 'Could not load configuration source.',
                    detail: (e as { message?: string })?.message,
                    onRetry: refresh,
                })
            );
        }
    }

    function focusPath(path: string): void {
        const field = ALL_FIELDS.find((f) => f.path === path);
        if (!field) return;
        if (field.advanced && !showAdvanced) {
            showAdvanced = true;
            renderForm();
        }
        const control = controls.find((c) => c.field.path === path);
        if (!control) return;
        control.root.scrollIntoView({ block: 'center', behavior: 'smooth' });
        const ring = ['ring-2', 'ring-cyan-500/50', 'rounded-lg', 'transition'];
        control.root.classList.add(...ring);
        setTimeout(() => control.root.classList.remove(...ring), 1600);
        (control.root.querySelector('input, select, button') as HTMLElement | null)?.focus();
    }

    void refresh();
    return {
        refresh,
        focusPath,
        dirtyCount: () => dirtyControls().length,
        isDirty: () => dirtyControls().length > 0,
    };
}

/** Exposed for tests: the full set of managed paths. */
export const MANAGED_PATHS = ALL_FIELDS.map((f) => f.path);
