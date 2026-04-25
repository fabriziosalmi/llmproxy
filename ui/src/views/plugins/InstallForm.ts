/**
 * Plugin install form. Same shape as the legacy version: name + hook +
 * entrypoint + timeout + fail-policy + description. Uses Input primitives
 * for the text inputs and styled native <select>s for the two enums.
 */
import { createButton, createCard, createInput, cx } from '../../ui';
import { rum } from '../../services/rum';
import { RING_OPTIONS, type InstallPluginInput, type RingHook } from './types';

export interface InstallFormDeps {
    submit: (input: InstallPluginInput) => Promise<{ status?: string; detail?: string } | unknown>;
    onSuccess?: () => void;
    toast?: (message: string, kind?: 'success' | 'error' | 'warning' | 'info', durationMs?: number) => void;
}

export interface InstallFormHandle {
    root: HTMLElement;
    open(): void;
    close(): void;
    isOpen(): boolean;
}

const NAME_PATTERN = /^[a-z_][a-z0-9_]*$/i;

function makeSelect(
    id: string,
    label: string,
    options: Array<{ value: string; label: string }>
): {
    wrap: HTMLElement;
    select: HTMLSelectElement;
} {
    const wrap = document.createElement('div');
    wrap.className = 'flex flex-col gap-1';
    const lab = document.createElement('label');
    lab.htmlFor = id;
    lab.className = 'text-[10px] font-bold text-slate-500 uppercase tracking-widest';
    lab.textContent = label;
    const select = document.createElement('select');
    select.id = id;
    select.name = id;
    select.setAttribute('data-testid', id);
    select.setAttribute('aria-label', label);
    select.className =
        'bg-white/5 border border-white/10 rounded-lg px-3 py-1.5 text-[11px] text-white font-mono ' +
        'focus:outline-none focus:border-emerald-500/50';
    for (const o of options) {
        const opt = document.createElement('option');
        opt.value = o.value;
        opt.textContent = o.label;
        select.appendChild(opt);
    }
    wrap.appendChild(lab);
    wrap.appendChild(select);
    return { wrap, select };
}

export function createInstallPluginForm(deps: InstallFormDeps): InstallFormHandle {
    const nameField = createInput({
        name: 'install-name',
        label: 'Name',
        required: true,
        placeholder: 'my_plugin',
        testId: 'install-name',
    });
    const entrypointField = createInput({
        name: 'install-entrypoint',
        label: 'Entrypoint',
        required: true,
        placeholder: 'marketplace/my_plugin:MyPlugin',
        testId: 'install-entrypoint',
    });
    const timeoutField = createInput({
        name: 'install-timeout',
        label: 'Timeout (ms)',
        type: 'number',
        value: '500',
        testId: 'install-timeout',
    });
    const descriptionField = createInput({
        name: 'install-description',
        label: 'Description',
        placeholder: 'What does this plugin do?',
        testId: 'install-description',
    });

    const hook = makeSelect('install-hook', 'Hook (Ring) *', RING_OPTIONS);
    const failPolicy = makeSelect('install-fail-policy', 'Fail Policy', [
        { value: 'open', label: 'open (fail-open)' },
        { value: 'closed', label: 'closed (fail-closed)' },
    ]);

    const grid1 = document.createElement('div');
    grid1.className = 'grid grid-cols-1 md:grid-cols-2 gap-3 mb-3';
    grid1.appendChild(nameField.root);
    grid1.appendChild(hook.wrap);
    grid1.appendChild(entrypointField.root);
    grid1.appendChild(timeoutField.root);

    const grid2 = document.createElement('div');
    grid2.className = 'grid grid-cols-1 md:grid-cols-2 gap-3 mb-3';
    grid2.appendChild(failPolicy.wrap);
    grid2.appendChild(descriptionField.root);

    const heading = document.createElement('h3');
    heading.className = 'text-[11px] font-bold text-white mb-3';
    heading.textContent = 'Install Plugin';

    const cancelBtn = createButton({ label: 'Cancel', size: 'sm', variant: 'ghost', testId: 'install-cancel-btn' });
    const submitBtn = createButton({
        label: 'Install & Hot-Swap',
        size: 'sm',
        variant: 'primary',
        testId: 'install-submit-btn',
    });

    const actions = document.createElement('div');
    actions.className = 'flex items-center justify-end gap-2';
    actions.appendChild(cancelBtn);
    actions.appendChild(submitBtn);

    const body = document.createElement('div');
    body.appendChild(heading);
    body.appendChild(grid1);
    body.appendChild(grid2);
    body.appendChild(actions);

    const root = createCard({
        body,
        className: cx('hidden mb-4 border-emerald-500/20'),
        testId: 'plugin-install-form',
    });

    function open(): void {
        root.classList.remove('hidden');
        nameField.input.focus();
    }
    function close(): void {
        root.classList.add('hidden');
        nameField.setValue('');
        entrypointField.setValue('');
        descriptionField.setValue('');
        nameField.setError(null);
        entrypointField.setError(null);
    }
    function isOpen(): boolean {
        return !root.classList.contains('hidden');
    }

    cancelBtn.addEventListener('click', () => close());

    submitBtn.addEventListener('click', async () => {
        const name = nameField.getValue().trim();
        const entrypoint = entrypointField.getValue().trim();

        let firstInvalid: HTMLInputElement | null = null;
        if (!name) {
            nameField.setError('Required.');
            firstInvalid = nameField.input;
        } else if (!NAME_PATTERN.test(name)) {
            nameField.setError(
                'Use letters, digits or underscore. Must start with a letter or underscore (Python module convention).'
            );
            firstInvalid = nameField.input;
        }
        if (!entrypoint) {
            entrypointField.setError('Required.');
            firstInvalid ??= entrypointField.input;
        } else if (!entrypoint.includes(':')) {
            entrypointField.setError("Expected 'module.path:ClassName'.");
            firstInvalid ??= entrypointField.input;
        }
        if (firstInvalid) {
            firstInvalid.focus();
            return;
        }

        const payload: InstallPluginInput = {
            name,
            hook: (hook.select.value as RingHook) || 'pre_flight',
            entrypoint,
            type: 'python',
            timeout_ms: Number.parseInt(timeoutField.getValue(), 10) || 500,
            fail_policy: (failPolicy.select.value as 'open' | 'closed') || 'open',
            description: descriptionField.getValue().trim(),
            enabled: true,
        };

        const submitEl = submitBtn as HTMLButtonElement;
        submitEl.disabled = true;
        const labelSpan = submitEl.querySelector('span:last-child');
        const original = labelSpan?.textContent ?? 'Install & Hot-Swap';
        if (labelSpan) labelSpan.textContent = 'Installing…';

        rum.action('plugin_install', { name, hook: payload.hook });
        try {
            const result = (await deps.submit(payload)) as { status?: string; detail?: string };
            if (result?.status === 'installed') {
                deps.toast?.(`Plugin "${name}" installed successfully`, 'success');
                close();
                deps.onSuccess?.();
            } else {
                deps.toast?.(`Install failed: ${result?.detail ?? JSON.stringify(result)}`, 'error', 5_000);
            }
        } catch (err) {
            deps.toast?.(`Install error: ${(err as Error)?.message ?? err}`, 'error', 5_000);
        } finally {
            submitEl.disabled = false;
            if (labelSpan) labelSpan.textContent = original;
        }
    });

    return { root, open, close, isOpen };
}
