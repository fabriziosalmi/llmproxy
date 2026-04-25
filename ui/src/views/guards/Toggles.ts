import { createCard, createToggle } from '../../ui';
import type { ToggleHandle } from '../../ui';

export interface MountToggleOptions {
    title: string;
    description: string;
    initialChecked: boolean;
    /** Async backend call; resolves with the canonical new state. */
    onToggle: (next: boolean) => Promise<{ enabled: boolean }>;
    /** Toast helper. */
    toast?: (message: string, kind?: 'success' | 'error' | 'warning' | 'info') => void;
    successLabel?: (enabled: boolean) => string;
    failureLabel?: (err: string) => string;
    testId?: string;
}

/**
 * Mount a single big-toggle card (proxy enable, priority steering, …) into
 * `container`. The card includes a title, description, and a single Toggle
 * primitive that talks to the backend, surfaces toasts, and reverts on
 * failure.
 */
export function mountToggleCard(container: HTMLElement, opts: MountToggleOptions): ToggleHandle {
    const description = document.createElement('p');
    description.className = 'text-[10px] text-slate-500 mt-0.5';
    description.textContent = opts.description;

    const heading = document.createElement('h2');
    heading.className = 'text-sm font-bold text-white';
    heading.textContent = opts.title;

    const left = document.createElement('div');
    left.appendChild(heading);
    left.appendChild(description);

    const row = document.createElement('div');
    row.className = 'flex items-center justify-between';
    row.appendChild(left);

    const toggle = createToggle({
        label: opts.title,
        checked: opts.initialChecked,
        testId: opts.testId,
    });
    // Strip the toggle's own internal label — we already render heading/desc above.
    toggle.root.replaceChildren(toggle.root.querySelector('[role="switch"]')!);
    row.appendChild(toggle.root);

    const card = createCard({ body: row, className: 'mb-6', elevation: 'flat' });
    container.replaceChildren(card);

    // Attach the click handler AFTER mount so we can intercept the toggle's
    // internal flip and revert on failure.
    const switchEl = toggle.root.querySelector<HTMLButtonElement>('[role="switch"]')!;
    switchEl.addEventListener(
        'click',
        async (e) => {
            // Prevent the primitive's flip — we'll drive it manually after the
            // backend confirms (or revert on failure).
            e.stopImmediatePropagation();
            const desired = !toggle.isChecked();
            switchEl.setAttribute('disabled', '');
            try {
                const res = await opts.onToggle(desired);
                toggle.setChecked(res.enabled);
                opts.toast?.(
                    opts.successLabel?.(res.enabled) ?? `${opts.title} ${res.enabled ? 'enabled' : 'disabled'}`,
                    'success'
                );
            } catch (err) {
                const msg = (err as Error)?.message ?? String(err);
                opts.toast?.(opts.failureLabel?.(msg) ?? `${opts.title} failed: ${msg}`, 'error');
            } finally {
                switchEl.removeAttribute('disabled');
            }
        },
        { capture: true }
    );

    return toggle;
}
