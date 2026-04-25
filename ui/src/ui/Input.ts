/**
 * Input primitive — labeled form field with optional help text and inline
 * error. Returns the wrapper element plus an API that mutates the field
 * without forcing a re-render of the parent.
 *
 * The label is wired to the input via `for`/`id`. The error block is
 * referenced by `aria-describedby` only when visible, so screen readers
 * announce it on submit failure but skip it when the field is clean.
 */
import { cx } from './classnames';

export type InputType = 'text' | 'password' | 'email' | 'number' | 'url' | 'search';

export interface InputFieldOptions {
    /** Field id + name. Used for the label-for / input-id link. */
    name: string;
    label: string;
    type?: InputType;
    placeholder?: string;
    value?: string;
    required?: boolean;
    /** Pinned-below help text. Hidden when an error is shown. */
    helpText?: string;
    /** Initial error message; can be cleared/set later via setError. */
    error?: string | null;
    onInput?: (value: string, ev: Event) => void;
    onChange?: (value: string, ev: Event) => void;
    autoComplete?: string;
    className?: string;
    testId?: string;
}

export interface InputFieldHandle {
    root: HTMLElement;
    input: HTMLInputElement;
    setError(msg: string | null): void;
    setValue(value: string): void;
    getValue(): string;
}

export function createInput(opts: InputFieldOptions): InputFieldHandle {
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

    const input = document.createElement('input');
    input.id = opts.name;
    input.name = opts.name;
    input.type = opts.type ?? 'text';
    input.value = opts.value ?? '';
    if (opts.placeholder) input.placeholder = opts.placeholder;
    if (opts.required) input.required = true;
    if (opts.autoComplete) input.setAttribute('autocomplete', opts.autoComplete);
    input.className =
        'bg-white/5 border border-white/10 rounded-lg px-3 py-1.5 text-[11px] text-white font-mono ' +
        'focus:outline-none focus:border-cyan-500/50 focus-visible:ring-2 focus-visible:ring-cyan-500/30 ' +
        'aria-[invalid=true]:border-rose-500/60';
    root.appendChild(input);

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
        if (ids.length) input.setAttribute('aria-describedby', ids.join(' '));
        else input.removeAttribute('aria-describedby');
    };

    const setError = (msg: string | null): void => {
        if (msg) {
            errEl.textContent = msg;
            errEl.classList.remove('hidden');
            help.classList.add('hidden');
            input.setAttribute('aria-invalid', 'true');
        } else {
            errEl.classList.add('hidden');
            errEl.textContent = '';
            input.removeAttribute('aria-invalid');
            if (opts.helpText) help.classList.remove('hidden');
        }
        updateAria();
    };

    if (opts.error) setError(opts.error);
    else updateAria();

    if (opts.onInput) {
        input.addEventListener('input', (ev) => {
            // Auto-clear error on next keystroke; user is correcting.
            if (errEl.textContent) setError(null);
            opts.onInput?.(input.value, ev);
        });
    } else {
        input.addEventListener('input', () => {
            if (errEl.textContent) setError(null);
        });
    }
    if (opts.onChange) input.addEventListener('change', (ev) => opts.onChange?.(input.value, ev));

    return {
        root,
        input,
        setError,
        setValue(v: string): void {
            input.value = v;
        },
        getValue(): string {
            return input.value;
        },
    };
}
