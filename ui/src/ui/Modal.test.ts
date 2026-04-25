import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { confirm, createModal, prompt } from './Modal';

function getDialog(): HTMLElement | null {
    return document.querySelector('[role="dialog"]');
}

describe('createModal', () => {
    afterEach(() => {
        // Strip any leftover host between tests for clean isolation.
        document.getElementById('llmproxy-modal-host')?.remove();
    });

    it('mounts a role=dialog with aria-labelledby pointing at the title', () => {
        void createModal({
            title: 'Title',
            body: 'Body text',
            buttons: [{ label: 'OK', value: 'ok', role: 'primary' }],
        });
        const dlg = getDialog();
        expect(dlg).not.toBeNull();
        expect(dlg?.getAttribute('aria-modal')).toBe('true');
        const labelId = dlg?.getAttribute('aria-labelledby');
        expect(labelId).toBeTruthy();
        expect(document.getElementById(labelId!)?.textContent).toBe('Title');
    });

    it('resolves with the chosen button value and removes the panel', async () => {
        const p = createModal<string>({
            title: 'Pick',
            buttons: [
                { label: 'A', value: 'aa', role: 'ghost' },
                { label: 'B', value: 'bb', role: 'primary' },
            ],
        });
        // Click the primary button.
        const btnB = Array.from(document.querySelectorAll<HTMLButtonElement>('button')).find(
            (b) => b.textContent === 'B'
        );
        btnB?.click();
        await expect(p).resolves.toBe('bb');
        expect(getDialog()).toBeNull();
    });

    it('resolves undefined on Escape key', async () => {
        const p = createModal<string>({
            title: 'X',
            buttons: [{ label: 'Only', value: 'kept', role: 'primary' }],
        });
        getDialog()?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
        await expect(p).resolves.toBeUndefined();
    });

    it('danger flag flips the panel border and primary button colour', () => {
        void createModal({
            title: 'Delete?',
            danger: true,
            buttons: [
                { label: 'Cancel', value: false, role: 'ghost' },
                { label: 'Delete', value: true, role: 'primary' },
            ],
        });
        const panel = getDialog();
        expect(panel?.className).toContain('border-rose-500/30');
        const primary = Array.from(document.querySelectorAll<HTMLButtonElement>('button')).find(
            (b) => b.textContent === 'Delete'
        );
        expect(primary?.className).toContain('bg-rose-500/20');
    });

    it('keeps the modal open when a primary value function returns undefined', async () => {
        let attempts = 0;
        const p = createModal<string>({
            title: 'Validate',
            buttons: [
                { label: 'Cancel', value: null as unknown as string, role: 'ghost' },
                {
                    label: 'OK',
                    role: 'primary',
                    value: () => {
                        attempts++;
                        return attempts < 2 ? undefined : 'finally';
                    },
                },
            ],
        });
        const ok = Array.from(document.querySelectorAll<HTMLButtonElement>('button')).find(
            (b) => b.textContent === 'OK'
        )!;
        ok.click(); // first attempt → undefined → modal stays open
        expect(getDialog()).not.toBeNull();
        ok.click(); // second attempt → 'finally' → resolves and closes
        await expect(p).resolves.toBe('finally');
        expect(getDialog()).toBeNull();
    });
});

describe('confirm()', () => {
    afterEach(() => {
        document.getElementById('llmproxy-modal-host')?.remove();
    });

    it('returns true when the primary button is clicked', async () => {
        const p = confirm({ title: 'Sure?', message: 'Really?' });
        document.querySelector<HTMLButtonElement>('[data-testid="modal-confirm-ok"]')?.click();
        await expect(p).resolves.toBe(true);
    });

    it('returns false when Cancel is clicked', async () => {
        const p = confirm({ title: 'Sure?' });
        document.querySelector<HTMLButtonElement>('[data-testid="modal-confirm-cancel"]')?.click();
        await expect(p).resolves.toBe(false);
    });

    it('returns false when dismissed (Escape)', async () => {
        const p = confirm({ title: 'X' });
        getDialog()?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
        await expect(p).resolves.toBe(false);
    });
});

describe('prompt()', () => {
    afterEach(() => {
        document.getElementById('llmproxy-modal-host')?.remove();
    });

    it('returns the entered value when the primary button is clicked', async () => {
        const p = prompt({ title: 'Name', defaultValue: 'fab' });
        const input = document.querySelector<HTMLInputElement>('[data-testid="modal-prompt-input"]')!;
        input.value = 'fab2';
        document.querySelector<HTMLButtonElement>('[data-testid="modal-prompt-ok"]')?.click();
        await expect(p).resolves.toBe('fab2');
    });

    it('returns null when Cancel is clicked', async () => {
        const p = prompt({ title: 'Name' });
        document.querySelector<HTMLButtonElement>('[data-testid="modal-prompt-cancel"]')?.click();
        await expect(p).resolves.toBeNull();
    });

    it('keeps the modal open and shows the validation message when validate() returns a string', async () => {
        let validateCalls = 0;
        const p = prompt({
            title: 'Url',
            validate: (v) => {
                validateCalls++;
                return v.startsWith('http') ? null : 'must start with http';
            },
        });
        const input = document.querySelector<HTMLInputElement>('[data-testid="modal-prompt-input"]')!;
        const ok = document.querySelector<HTMLButtonElement>('[data-testid="modal-prompt-ok"]')!;

        input.value = 'ftp://nope';
        ok.click();
        // Modal stays open — error message visible.
        expect(getDialog()).not.toBeNull();
        const err = document.querySelector('[role="alert"]');
        expect(err?.textContent).toContain('must start with http');

        input.value = 'http://ok';
        ok.click();
        await expect(p).resolves.toBe('http://ok');
        expect(validateCalls).toBe(2);
    });
});

vi.mock('animate-css-noop', () => ({})); // placeholder, no-op — keeps the file lint-clean if ever imported
beforeEach(() => {
    /* clean DOM mounted from previous tests */
    document.getElementById('llmproxy-modal-host')?.remove();
});
