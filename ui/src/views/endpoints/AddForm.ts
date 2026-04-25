/**
 * Add-endpoint form. Composed of Input primitives + a styled native <select>
 * for the provider dropdown (we don't ship a Select primitive yet — when we
 * do, swap it in here).
 *
 * Returns a handle exposing `open()` / `close()` / `focus()` so the caller
 * (orchestrator, onboarding empty-state) can drive it.
 */
import { createButton, createCard, createInput, cx } from '../../ui';
import type { InputFieldHandle } from '../../ui';
import { rum } from '../../services/rum';
import { PROVIDER_OPTIONS, type AddEndpointInput } from './types';

export interface AddFormDeps {
    /** Posts the new endpoint. Resolves with the created record on success. */
    submit: (input: AddEndpointInput) => Promise<unknown>;
    onSuccess?: (id: string) => void;
    toast?: (message: string, kind?: 'success' | 'error' | 'warning' | 'info') => void;
}

export interface AddFormHandle {
    root: HTMLElement;
    open(): void;
    close(): void;
    focus(): void;
    isOpen(): boolean;
}

const ID_PATTERN = /^[a-z0-9][a-z0-9_-]*$/i;

export function createAddEndpointForm(deps: AddFormDeps): AddFormHandle {
    const idField = createInput({
        name: 'ep-name',
        label: 'Name / ID',
        placeholder: 'my-openai',
        required: true,
        helpText: 'Letters, digits, - or _',
        testId: 'ep-name',
    });

    const urlField = createInput({
        name: 'ep-url',
        label: 'Base URL',
        placeholder: 'https://api.openai.com/v1',
        type: 'url',
        required: true,
        testId: 'ep-url',
    });

    const priorityField = createInput({
        name: 'ep-priority',
        label: 'Priority',
        type: 'number',
        value: '0',
        testId: 'ep-priority',
    });

    const apiKeyField = createInput({
        name: 'ep-api-key',
        label: 'API key (optional — leave blank for no-auth local servers)',
        type: 'password',
        placeholder: 'sk-…',
        autoComplete: 'new-password',
        testId: 'ep-api-key',
    });

    const modelsField = createInput({
        name: 'ep-models',
        label: 'Models (comma-separated)',
        placeholder: 'gpt-4o, gpt-4o-mini',
        testId: 'ep-models',
    });

    // Provider <select> — styled to match the surrounding inputs.
    const providerWrap = document.createElement('div');
    providerWrap.className = 'flex flex-col gap-1';
    const providerLabel = document.createElement('label');
    providerLabel.htmlFor = 'ep-provider';
    providerLabel.className = 'text-[9px] font-bold text-slate-500 uppercase tracking-widest';
    providerLabel.textContent = 'Provider *';
    const providerSelect = document.createElement('select');
    providerSelect.id = 'ep-provider';
    providerSelect.name = 'ep-provider';
    providerSelect.setAttribute('aria-label', 'Endpoint provider');
    providerSelect.setAttribute('data-testid', 'ep-provider');
    providerSelect.className =
        'bg-white/5 border border-white/10 rounded-lg px-3 py-1.5 text-[11px] text-white font-mono ' +
        'focus:outline-none focus:border-cyan-500/50 focus-visible:ring-2 focus-visible:ring-cyan-500/30';
    for (const opt of PROVIDER_OPTIONS) {
        const o = document.createElement('option');
        o.value = opt.value;
        o.textContent = opt.label;
        providerSelect.appendChild(o);
    }
    providerWrap.appendChild(providerLabel);
    providerWrap.appendChild(providerSelect);

    const grid = document.createElement('div');
    grid.className = 'grid grid-cols-1 md:grid-cols-2 gap-3 mb-3';
    grid.appendChild(idField.root);
    grid.appendChild(urlField.root);
    grid.appendChild(providerWrap);
    grid.appendChild(priorityField.root);
    apiKeyField.root.classList.add('md:col-span-2');
    grid.appendChild(apiKeyField.root);
    modelsField.root.classList.add('md:col-span-2');
    grid.appendChild(modelsField.root);

    const heading = document.createElement('h3');
    heading.className = 'text-[11px] font-bold text-white mb-3';
    heading.textContent = 'Add LLM Endpoint';

    const cancelBtn = createButton({ label: 'Cancel', variant: 'ghost', size: 'sm', testId: 'ep-cancel-btn' });
    const submitBtn = createButton({
        label: 'Add Endpoint',
        variant: 'primary',
        size: 'sm',
        testId: 'ep-add-btn',
    });

    const actions = document.createElement('div');
    actions.className = 'flex items-center justify-end gap-2';
    actions.appendChild(cancelBtn);
    actions.appendChild(submitBtn);

    const body = document.createElement('div');
    body.appendChild(heading);
    body.appendChild(grid);
    body.appendChild(actions);

    const root = createCard({
        body,
        className: cx('hidden mb-4 border-emerald-500/20'),
        testId: 'add-endpoint-form',
    });

    function open(): void {
        root.classList.remove('hidden');
        idField.input.focus();
    }
    function close(): void {
        root.classList.add('hidden');
        // Reset the user-editable fields, leave provider + priority at their defaults.
        idField.setValue('');
        urlField.setValue('');
        apiKeyField.setValue('');
        modelsField.setValue('');
        idField.setError(null);
        urlField.setError(null);
    }
    function isOpen(): boolean {
        return !root.classList.contains('hidden');
    }

    cancelBtn.addEventListener('click', () => close());

    submitBtn.addEventListener('click', async () => {
        const id = idField.getValue().trim();
        const url = urlField.getValue().trim();

        let firstInvalid: HTMLInputElement | null = null;
        if (!id) {
            idField.setError('Required.');
            firstInvalid = idField.input;
        } else if (!ID_PATTERN.test(id)) {
            idField.setError('Use letters, digits, - or _ (must start with a letter or digit).');
            firstInvalid = idField.input;
        }

        if (!url) {
            urlField.setError('Required.');
            firstInvalid ??= urlField.input;
        } else {
            try {
                const u = new URL(url);
                if (u.protocol !== 'http:' && u.protocol !== 'https:') {
                    throw new Error('Only http:// and https:// are supported.');
                }
            } catch (err) {
                urlField.setError((err as Error)?.message || 'Not a valid URL.');
                firstInvalid ??= urlField.input;
            }
        }

        if (firstInvalid) {
            firstInvalid.focus();
            return;
        }

        const provider = providerSelect.value;
        const priority = Number.parseInt(priorityField.getValue(), 10) || 0;
        const api_key = apiKeyField.getValue();
        const models = modelsField
            .getValue()
            .split(',')
            .map((s) => s.trim())
            .filter(Boolean);

        const payload: AddEndpointInput = { id, url, provider, priority, models };
        if (api_key) payload.api_key = api_key;

        // Disable the submit during the in-flight request.
        const submitEl = submitBtn as HTMLButtonElement;
        submitEl.disabled = true;
        const labelSpan = submitEl.querySelector('span:last-child');
        const originalLabel = labelSpan?.textContent ?? 'Add Endpoint';
        if (labelSpan) labelSpan.textContent = 'Adding…';

        rum.action('endpoint_add', { provider });
        try {
            await deps.submit(payload);
            deps.toast?.(`Endpoint "${id}" added`, 'success');
            close();
            deps.onSuccess?.(id);
        } catch (err) {
            const msg = (err as Error)?.message ?? String(err);
            deps.toast?.(`Failed: ${msg}`, 'error');
        } finally {
            submitEl.disabled = false;
            if (labelSpan) labelSpan.textContent = originalLabel;
        }
    });

    return {
        root,
        open,
        close,
        focus(): void {
            idField.input.focus();
        },
        isOpen,
    };
}

// Export internal field handles for tests when needed.
export type { InputFieldHandle };
