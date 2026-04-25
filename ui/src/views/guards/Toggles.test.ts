import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mountToggleCard } from './Toggles';

let container: HTMLElement;

beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
});

afterEach(() => {
    container.remove();
});

describe('mountToggleCard', () => {
    it('renders the title, description, and a role=switch with the initial state', () => {
        mountToggleCard(container, {
            title: 'Gateway Status',
            description: 'Master proxy enable/disable',
            initialChecked: true,
            onToggle: async () => ({ enabled: true }),
        });
        expect(container.textContent).toContain('Gateway Status');
        expect(container.textContent).toContain('Master proxy enable/disable');
        const sw = container.querySelector<HTMLButtonElement>('[role="switch"]')!;
        expect(sw.getAttribute('aria-checked')).toBe('true');
    });

    it('clicking calls onToggle with the desired state and reflects the canonical response', async () => {
        const onToggle = vi.fn().mockResolvedValue({ enabled: false });
        const toast = vi.fn();
        mountToggleCard(container, {
            title: 'Priority Steering',
            description: 'Route to highest-priority endpoint only',
            initialChecked: true,
            onToggle,
            toast,
        });
        const sw = container.querySelector<HTMLButtonElement>('[role="switch"]')!;
        sw.click();

        await new Promise((r) => setTimeout(r, 0));
        expect(onToggle).toHaveBeenCalledWith(false);
        expect(sw.getAttribute('aria-checked')).toBe('false');
        expect(toast).toHaveBeenCalledWith(expect.stringContaining('Priority Steering disabled'), 'success');
    });

    it('the switch is disabled while the request is in flight', async () => {
        let resolve!: (v: { enabled: boolean }) => void;
        const onToggle = vi.fn(
            () =>
                new Promise<{ enabled: boolean }>((r) => {
                    resolve = r;
                })
        );
        mountToggleCard(container, {
            title: 'Gateway',
            description: 'desc',
            initialChecked: false,
            onToggle,
        });
        const sw = container.querySelector<HTMLButtonElement>('[role="switch"]')!;
        sw.click();
        // disabled attribute set while pending.
        expect(sw.hasAttribute('disabled')).toBe(true);
        resolve({ enabled: true });
        await new Promise((r) => setTimeout(r, 0));
        expect(sw.hasAttribute('disabled')).toBe(false);
        expect(sw.getAttribute('aria-checked')).toBe('true');
    });

    it('a failing onToggle keeps the previous state and surfaces an error toast', async () => {
        const onToggle = vi.fn().mockRejectedValue(new Error('501 not implemented'));
        const toast = vi.fn();
        mountToggleCard(container, {
            title: 'Gateway',
            description: 'desc',
            initialChecked: true,
            onToggle,
            toast,
        });
        const sw = container.querySelector<HTMLButtonElement>('[role="switch"]')!;
        sw.click();

        await new Promise((r) => setTimeout(r, 0));
        // State unchanged after failure — primitive's flip never ran.
        expect(sw.getAttribute('aria-checked')).toBe('true');
        expect(toast).toHaveBeenCalledWith(expect.stringContaining('501'), 'error');
    });
});
