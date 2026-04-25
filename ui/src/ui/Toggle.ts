/**
 * Toggle primitive — accessible on/off switch.
 *
 * Uses a `role="switch"` button so screen readers announce "switch, on/off"
 * rather than "checkbox". Click and Space/Enter both flip the state.
 * Visually aligned with Apple's segmented look but kept compact for use in
 * dense rows (Guards, Settings, per-plugin enable).
 */
import { cx } from './classnames';

export interface ToggleOptions {
    label: string;
    checked?: boolean;
    disabled?: boolean;
    /** Optional secondary description shown beside the label. */
    description?: string;
    /** Fired on every state flip. The new state is passed as the first arg. */
    onChange?: (checked: boolean) => void;
    className?: string;
    testId?: string;
}

export interface ToggleHandle {
    root: HTMLElement;
    isChecked(): boolean;
    setChecked(checked: boolean, fire?: boolean): void;
    setDisabled(disabled: boolean): void;
}

export function createToggle(opts: ToggleOptions): ToggleHandle {
    let checked = !!opts.checked;
    let disabled = !!opts.disabled;

    const root = document.createElement('div');
    root.className = cx('flex items-center justify-between gap-3', opts.className);
    if (opts.testId) root.setAttribute('data-testid', opts.testId);

    // Text column
    const text = document.createElement('div');
    text.className = 'flex flex-col';
    const labelEl = document.createElement('span');
    labelEl.className = 'text-[11px] font-semibold text-slate-200';
    labelEl.textContent = opts.label;
    text.appendChild(labelEl);
    if (opts.description) {
        const desc = document.createElement('span');
        desc.className = 'text-[10px] text-slate-500';
        desc.textContent = opts.description;
        text.appendChild(desc);
    }
    root.appendChild(text);

    // Switch
    const switchEl = document.createElement('button');
    switchEl.type = 'button';
    switchEl.setAttribute('role', 'switch');
    switchEl.setAttribute('aria-label', opts.label);
    switchEl.className = cx(
        'relative inline-flex shrink-0 h-5 w-9 rounded-full transition-colors',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500/40',
        'disabled:opacity-50 disabled:cursor-not-allowed'
    );
    const thumb = document.createElement('span');
    thumb.setAttribute('aria-hidden', 'true');
    thumb.className = 'absolute top-0.5 inline-block h-4 w-4 rounded-full bg-white shadow transition-transform';
    switchEl.appendChild(thumb);
    root.appendChild(switchEl);

    const paint = (): void => {
        switchEl.setAttribute('aria-checked', String(checked));
        switchEl.classList.toggle('bg-cyan-500/60', checked);
        switchEl.classList.toggle('bg-white/10', !checked);
        thumb.classList.toggle('translate-x-[18px]', checked);
        thumb.classList.toggle('translate-x-0.5', !checked);
        if (disabled) switchEl.setAttribute('disabled', '');
        else switchEl.removeAttribute('disabled');
    };

    const flip = (fire: boolean): void => {
        if (disabled) return;
        checked = !checked;
        paint();
        if (fire) opts.onChange?.(checked);
    };

    switchEl.addEventListener('click', () => flip(true));
    switchEl.addEventListener('keydown', (e) => {
        if (e.key === ' ' || e.key === 'Enter') {
            e.preventDefault();
            flip(true);
        }
    });

    paint();

    return {
        root,
        isChecked: () => checked,
        setChecked(next: boolean, fire = false): void {
            if (next === checked) return;
            checked = next;
            paint();
            if (fire) opts.onChange?.(checked);
        },
        setDisabled(next: boolean): void {
            disabled = next;
            paint();
        },
    };
}
