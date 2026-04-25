import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createInstallPluginForm } from './InstallForm';

let host: HTMLElement;

beforeEach(() => {
    host = document.createElement('div');
    document.body.appendChild(host);
});

afterEach(() => {
    host.remove();
});

describe('createInstallPluginForm', () => {
    it('mounts hidden by default; open() unhides and focuses Name', () => {
        const handle = createInstallPluginForm({ submit: vi.fn() });
        host.appendChild(handle.root);
        expect(handle.isOpen()).toBe(false);
        handle.open();
        expect(handle.isOpen()).toBe(true);
    });

    it('refuses submit with empty Name + Entrypoint', async () => {
        const submit = vi.fn();
        const handle = createInstallPluginForm({ submit });
        host.appendChild(handle.root);
        handle.open();
        handle.root.querySelector<HTMLButtonElement>('[data-testid="install-submit-btn"]')!.click();
        expect(submit).not.toHaveBeenCalled();
        const errs = handle.root.querySelectorAll('[role="alert"]:not(.hidden)');
        expect(errs.length).toBeGreaterThanOrEqual(2);
    });

    it('refuses a Name that is not a valid Python identifier', async () => {
        const submit = vi.fn();
        const handle = createInstallPluginForm({ submit });
        host.appendChild(handle.root);
        handle.open();
        const name = handle.root.querySelector<HTMLInputElement>('input#install-name')!;
        const ep = handle.root.querySelector<HTMLInputElement>('input#install-entrypoint')!;
        name.value = '0bad-name';
        ep.value = 'plugins:Plugin';
        handle.root.querySelector<HTMLButtonElement>('[data-testid="install-submit-btn"]')!.click();
        expect(submit).not.toHaveBeenCalled();
        expect(handle.root.querySelector('#install-name-err')?.textContent).toMatch(/letters, digits/i);
    });

    it('refuses an Entrypoint missing the colon separator', async () => {
        const submit = vi.fn();
        const handle = createInstallPluginForm({ submit });
        host.appendChild(handle.root);
        handle.open();
        handle.root.querySelector<HTMLInputElement>('input#install-name')!.value = 'ok_plugin';
        handle.root.querySelector<HTMLInputElement>('input#install-entrypoint')!.value = 'plugins.foo.NoColonHere';
        handle.root.querySelector<HTMLButtonElement>('[data-testid="install-submit-btn"]')!.click();
        expect(submit).not.toHaveBeenCalled();
        expect(handle.root.querySelector('#install-entrypoint-err')?.textContent).toMatch(/module\.path:ClassName/i);
    });

    it('happy path: posts the parsed payload, fires onSuccess + toast, closes', async () => {
        const submit = vi.fn().mockResolvedValue({ status: 'installed' });
        const onSuccess = vi.fn();
        const toast = vi.fn();
        const handle = createInstallPluginForm({ submit, onSuccess, toast });
        host.appendChild(handle.root);
        handle.open();

        handle.root.querySelector<HTMLInputElement>('input#install-name')!.value = 'pii_masker';
        handle.root.querySelector<HTMLInputElement>('input#install-entrypoint')!.value =
            'plugins.ring2.pii_masker:PIIMasker';
        handle.root.querySelector<HTMLInputElement>('input#install-timeout')!.value = '250';
        handle.root.querySelector<HTMLInputElement>('input#install-description')!.value = 'Mask PII';
        handle.root.querySelector<HTMLSelectElement>('select#install-hook')!.value = 'pre_flight';
        handle.root.querySelector<HTMLSelectElement>('select#install-fail-policy')!.value = 'closed';

        handle.root.querySelector<HTMLButtonElement>('[data-testid="install-submit-btn"]')!.click();
        await new Promise((r) => setTimeout(r, 0));

        expect(submit).toHaveBeenCalledWith({
            name: 'pii_masker',
            hook: 'pre_flight',
            entrypoint: 'plugins.ring2.pii_masker:PIIMasker',
            type: 'python',
            timeout_ms: 250,
            fail_policy: 'closed',
            description: 'Mask PII',
            enabled: true,
        });
        expect(toast).toHaveBeenCalledWith(expect.stringContaining('pii_masker'), 'success');
        expect(onSuccess).toHaveBeenCalled();
        expect(handle.isOpen()).toBe(false);
    });

    it('a backend "status: failed" surfaces the detail message and keeps the form open', async () => {
        const submit = vi.fn().mockResolvedValue({ status: 'failed', detail: 'entrypoint module not found' });
        const toast = vi.fn();
        const handle = createInstallPluginForm({ submit, toast });
        host.appendChild(handle.root);
        handle.open();
        handle.root.querySelector<HTMLInputElement>('input#install-name')!.value = 'p';
        handle.root.querySelector<HTMLInputElement>('input#install-entrypoint')!.value = 'a:B';
        handle.root.querySelector<HTMLButtonElement>('[data-testid="install-submit-btn"]')!.click();
        await new Promise((r) => setTimeout(r, 0));
        expect(toast).toHaveBeenCalledWith(expect.stringContaining('module not found'), 'error', 5_000);
        expect(handle.isOpen()).toBe(true);
    });

    it('a thrown error surfaces a different toast and keeps the form open', async () => {
        const submit = vi.fn().mockRejectedValue(new Error('500 internal'));
        const toast = vi.fn();
        const handle = createInstallPluginForm({ submit, toast });
        host.appendChild(handle.root);
        handle.open();
        handle.root.querySelector<HTMLInputElement>('input#install-name')!.value = 'p';
        handle.root.querySelector<HTMLInputElement>('input#install-entrypoint')!.value = 'a:B';
        handle.root.querySelector<HTMLButtonElement>('[data-testid="install-submit-btn"]')!.click();
        await new Promise((r) => setTimeout(r, 0));
        expect(toast).toHaveBeenCalledWith(expect.stringContaining('500'), 'error', 5_000);
        expect(handle.isOpen()).toBe(true);
    });

    it('Cancel closes the form and resets the editable fields', () => {
        const handle = createInstallPluginForm({ submit: vi.fn() });
        host.appendChild(handle.root);
        handle.open();
        const name = handle.root.querySelector<HTMLInputElement>('input#install-name')!;
        name.value = 'cancel-me';
        handle.root.querySelector<HTMLButtonElement>('[data-testid="install-cancel-btn"]')!.click();
        expect(handle.isOpen()).toBe(false);
        handle.open();
        expect(name.value).toBe('');
    });
});
