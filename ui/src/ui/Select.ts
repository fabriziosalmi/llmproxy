/**
 * Select primitive — labeled dropdown with optional help text and inline error,
 * the select-shaped sibling of `createInput`. Extracted from the hand-rolled
 * `<select>` in endpoints/AddForm so enum config fields get the same
 * label / help / error / ARIA treatment as text inputs.
 */
import { cx } from './classnames';

export interface SelectOption {
    value: string;
    label: string;
}

export interface SelectFieldOptions {
    /** Field id + name. Used for the label-for / select-id link. */
    name: string;
    label: string;
    options: SelectOption[];
    value?: string;
    required?: boolean;
    /** Pinned-below help text. Hidden when an error is shown. */
    helpText?: string;
    error?: string | null;
    onChange?: (value: string, ev: Event) => void;
    className?: string;
    testId?: string;
}

export interface SelectFieldHandle {
    root: HTMLElement;
    select: HTMLSelectElement;
    setError(msg: string | null): void;
    setValue(value: string): void;
    getValue(): string;
}

export function createSelect(opts: SelectFieldOptions): SelectFieldHandle {
    const errId = `${opts.name}-err`;
    const helpId = `${opts.name}-help`;

    const root = document.createElement('div');
    root.className = cx('flex flex-col gap-1', opts.className);
    if (opts.testId) root.setAttribute('data-testid', opts.testId);

    const label = document.createElement('label');
    label.htmlFor = opts.name;
    label.className = 'text-[9px] font-bold text-slate-500 uppercase tracking-widest';
    label.textContent = opts.label + (opts.required ? ' *' : '');
    root.appendChild(label);

    const select = document.createElement('select');
    select.id = opts.name;
    select.name = opts.name;
    select.setAttribute('aria-label', opts.label);
    select.className =
        'bg-white/5 border border-white/10 rounded-lg px-3 py-1.5 text-[11px] text-white font-mono ' +
        'focus:outline-none focus:border-cyan-500/50 focus-visible:ring-2 focus-visible:ring-cyan-500/30 ' +
        'aria-[invalid=true]:border-rose-500/60';
    for (const opt of opts.options) {
        const o = document.createElement('option');
        o.value = opt.value;
        o.textContent = opt.label;
        select.appendChild(o);
    }
    if (opts.value != null) select.value = opts.value;
    root.appendChild(select);

    const help = document.createElement('p');
    help.id = helpId;
    help.className = 'text-[10px] text-slate-500';
    if (opts.helpText) help.textContent = opts.helpText;
    else help.classList.add('hidden');
    root.appendChild(help);

    const errEl = document.createElement('p');
    errEl.id = errId;
    errEl.setAttribute('role', 'alert');
    errEl.className = 'hidden text-[10px] text-rose-400 font-mono';
    root.appendChild(errEl);

    const updateAria = (): void => {
        const ids: string[] = [];
        if (!help.classList.contains('hidden')) ids.push(helpId);
        if (!errEl.classList.contains('hidden')) ids.push(errId);
        if (ids.length) select.setAttribute('aria-describedby', ids.join(' '));
        else select.removeAttribute('aria-describedby');
    };

    const setError = (msg: string | null): void => {
        if (msg) {
            errEl.textContent = msg;
            errEl.classList.remove('hidden');
            help.classList.add('hidden');
            select.setAttribute('aria-invalid', 'true');
        } else {
            errEl.classList.add('hidden');
            errEl.textContent = '';
            select.removeAttribute('aria-invalid');
            if (opts.helpText) help.classList.remove('hidden');
        }
        updateAria();
    };

    if (opts.error) setError(opts.error);
    else updateAria();

    select.addEventListener('change', (ev) => {
        if (errEl.textContent) setError(null);
        opts.onChange?.(select.value, ev);
    });

    return {
        root,
        select,
        setError,
        setValue(v: string): void {
            select.value = v;
        },
        getValue(): string {
            return select.value;
        },
    };
}
