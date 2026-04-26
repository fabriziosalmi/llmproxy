import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createSnippet } from './Snippet';

describe('createSnippet', () => {
    const installClipboard = (): { writeText: ReturnType<typeof vi.fn> } => {
        const writeText = vi.fn().mockResolvedValue(undefined);
        Object.defineProperty(navigator, 'clipboard', {
            configurable: true,
            value: { writeText },
        });
        return { writeText };
    };

    beforeEach(() => {
        document.body.innerHTML = '';
    });
    afterEach(() => {
        vi.restoreAllMocks();
    });

    it('renders the language tag, the code body, and a copy button', () => {
        const { root } = createSnippet({ language: 'cURL', code: 'curl https://example.com' });
        expect(root.textContent).toContain('cURL');
        const code = root.querySelector('pre code');
        expect(code?.textContent).toBe('curl https://example.com');
        const btn = root.querySelector('button[aria-label*="Copy"]');
        expect(btn).not.toBeNull();
    });

    it('caption is rendered above the header when supplied', () => {
        const { root } = createSnippet({
            language: 'Python',
            code: 'print(1)',
            caption: 'Smoke-test against this model.',
        });
        // Caption sits as the first text node — language tag comes after.
        expect(root.firstElementChild?.textContent).toBe('Smoke-test against this model.');
    });

    it('clicking copy fires navigator.clipboard.writeText with the exact code', async () => {
        const { writeText } = installClipboard();
        const { root } = createSnippet({ language: 'cURL', code: 'curl /v1/chat' });
        const btn = root.querySelector<HTMLButtonElement>('button[aria-label*="Copy"]')!;
        btn.click();
        // Resolve the promise + DOM flush.
        await new Promise((r) => setTimeout(r, 0));
        expect(writeText).toHaveBeenCalledWith('curl /v1/chat');
    });

    it('copy() handle returns true on success and updates the button', async () => {
        installClipboard();
        const handle = createSnippet({ language: 'cURL', code: 'echo hi' });
        const ok = await handle.copy();
        expect(ok).toBe(true);
        const btn = handle.root.querySelector<HTMLButtonElement>('button[aria-label*="Copy"]')!;
        expect(btn.textContent).toContain('copied');
    });

    it('copy() returns false when both clipboard paths fail', async () => {
        // No clipboard API + execCommand throws.
        Object.defineProperty(navigator, 'clipboard', { configurable: true, value: undefined });
        // happy-dom's document.execCommand may not exist; force a falsy outcome.
        (document as unknown as { execCommand: () => boolean }).execCommand = () => false;
        const handle = createSnippet({ language: 'cURL', code: 'echo hi' });
        const ok = await handle.copy();
        expect(ok).toBe(false);
        const btn = handle.root.querySelector<HTMLButtonElement>('button[aria-label*="Copy"]')!;
        expect(btn.textContent).toContain('failed');
    });

    it('testId is forwarded onto root + the copy button', () => {
        installClipboard();
        const { root } = createSnippet({ language: 'JS', code: 'a', testId: 'snip-js' });
        expect(root.getAttribute('data-testid')).toBe('snip-js');
        expect(root.querySelector('[data-testid="snip-js-copy"]')).not.toBeNull();
    });
});
