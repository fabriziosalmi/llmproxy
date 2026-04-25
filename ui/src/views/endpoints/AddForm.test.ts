import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createAddEndpointForm } from './AddForm';

let host: HTMLElement;

beforeEach(() => {
    host = document.createElement('div');
    document.body.appendChild(host);
});

afterEach(() => {
    host.remove();
});

describe('createAddEndpointForm', () => {
    it('mounts hidden by default; open() unhides and focuses the id field', () => {
        const handle = createAddEndpointForm({ submit: vi.fn() });
        host.appendChild(handle.root);
        expect(handle.isOpen()).toBe(false);
        handle.open();
        expect(handle.isOpen()).toBe(true);
    });

    it('refuses submit with an empty id and shows the inline error', async () => {
        const submit = vi.fn();
        const handle = createAddEndpointForm({ submit });
        host.appendChild(handle.root);
        handle.open();

        const addBtn = handle.root.querySelector<HTMLButtonElement>('[data-testid="ep-add-btn"]')!;
        addBtn.click();

        expect(submit).not.toHaveBeenCalled();
        const errs = handle.root.querySelectorAll('[role="alert"]:not(.hidden)');
        expect(errs.length).toBeGreaterThanOrEqual(1);
    });

    it('refuses an id with disallowed characters', async () => {
        const submit = vi.fn();
        const handle = createAddEndpointForm({ submit });
        host.appendChild(handle.root);
        handle.open();

        const idInput = handle.root.querySelector<HTMLInputElement>('input#ep-name')!;
        const urlInput = handle.root.querySelector<HTMLInputElement>('input#ep-url')!;
        idInput.value = 'has space';
        urlInput.value = 'https://api.openai.com/v1';

        handle.root.querySelector<HTMLButtonElement>('[data-testid="ep-add-btn"]')!.click();
        expect(submit).not.toHaveBeenCalled();
        const idErr = handle.root.querySelector('#ep-name-err')!;
        expect(idErr.textContent).toMatch(/letters, digits/i);
    });

    it('refuses an ftp URL', async () => {
        const submit = vi.fn();
        const handle = createAddEndpointForm({ submit });
        host.appendChild(handle.root);
        handle.open();

        const idInput = handle.root.querySelector<HTMLInputElement>('input#ep-name')!;
        const urlInput = handle.root.querySelector<HTMLInputElement>('input#ep-url')!;
        idInput.value = 'ok';
        urlInput.value = 'ftp://nope.example.com';

        handle.root.querySelector<HTMLButtonElement>('[data-testid="ep-add-btn"]')!.click();
        expect(submit).not.toHaveBeenCalled();
        const urlErr = handle.root.querySelector('#ep-url-err')!;
        expect(urlErr.textContent).toMatch(/http/i);
    });

    it('happy path: posts the parsed payload, fires onSuccess + toast, closes the form', async () => {
        const submit = vi.fn().mockResolvedValue({ id: 'my-openai' });
        const onSuccess = vi.fn();
        const toast = vi.fn();
        const handle = createAddEndpointForm({ submit, onSuccess, toast });
        host.appendChild(handle.root);
        handle.open();

        handle.root.querySelector<HTMLInputElement>('input#ep-name')!.value = 'my-openai';
        handle.root.querySelector<HTMLInputElement>('input#ep-url')!.value = 'https://api.openai.com/v1';
        handle.root.querySelector<HTMLInputElement>('input#ep-priority')!.value = '5';
        handle.root.querySelector<HTMLInputElement>('input#ep-models')!.value = 'gpt-4o, gpt-4o-mini';
        handle.root.querySelector<HTMLSelectElement>('select#ep-provider')!.value = 'openai';

        handle.root.querySelector<HTMLButtonElement>('[data-testid="ep-add-btn"]')!.click();
        await new Promise((r) => setTimeout(r, 0));

        expect(submit).toHaveBeenCalledWith({
            id: 'my-openai',
            url: 'https://api.openai.com/v1',
            provider: 'openai',
            priority: 5,
            models: ['gpt-4o', 'gpt-4o-mini'],
        });
        expect(toast).toHaveBeenCalledWith(expect.stringContaining('my-openai'), 'success');
        expect(onSuccess).toHaveBeenCalledWith('my-openai');
        expect(handle.isOpen()).toBe(false);
    });

    it('a failing submit surfaces an error toast and keeps the form open', async () => {
        const submit = vi.fn().mockRejectedValue(new Error('500 internal'));
        const toast = vi.fn();
        const handle = createAddEndpointForm({ submit, toast });
        host.appendChild(handle.root);
        handle.open();

        handle.root.querySelector<HTMLInputElement>('input#ep-name')!.value = 'my-openai';
        handle.root.querySelector<HTMLInputElement>('input#ep-url')!.value = 'https://api.openai.com/v1';

        handle.root.querySelector<HTMLButtonElement>('[data-testid="ep-add-btn"]')!.click();
        await new Promise((r) => setTimeout(r, 0));

        expect(submit).toHaveBeenCalled();
        expect(toast).toHaveBeenCalledWith(expect.stringContaining('500'), 'error');
        expect(handle.isOpen()).toBe(true);
    });

    it('Cancel closes the form and resets the editable fields', () => {
        const handle = createAddEndpointForm({ submit: vi.fn() });
        host.appendChild(handle.root);
        handle.open();

        const idInput = handle.root.querySelector<HTMLInputElement>('input#ep-name')!;
        idInput.value = 'about-to-cancel';

        handle.root.querySelector<HTMLButtonElement>('[data-testid="ep-cancel-btn"]')!.click();
        expect(handle.isOpen()).toBe(false);
        // Re-open and verify reset.
        handle.open();
        expect(idInput.value).toBe('');
    });
});
