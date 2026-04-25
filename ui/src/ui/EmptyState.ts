import { cx } from './classnames';
import { createButton, type ButtonOptions } from './Button';

export interface EmptyStateOptions {
    /** Short, descriptive headline — what's missing. */
    title: string;
    /** Plain explanation + the next thing to do. Keep under ~140 chars. */
    description?: string;
    /** Optional icon as raw SVG markup. */
    icon?: string;
    /** Optional primary CTA — typically links to docs or starts onboarding. */
    action?: ButtonOptions;
    /** Optional secondary CTA — usually a "learn more" link or doc pointer. */
    secondaryAction?: ButtonOptions;
    className?: string;
    testId?: string;
}

export function createEmptyState(opts: EmptyStateOptions): HTMLElement {
    const root = document.createElement('div');
    root.className = cx(
        'flex flex-col items-center justify-center text-center gap-3 px-6 py-10',
        'rounded-xl border border-dashed border-white/10 bg-white/[0.02]',
        opts.className
    );
    root.setAttribute('role', 'status');
    if (opts.testId) root.setAttribute('data-testid', opts.testId);

    if (opts.icon) {
        const iconHost = document.createElement('div');
        iconHost.className = 'w-10 h-10 flex items-center justify-center text-slate-500';
        iconHost.innerHTML = opts.icon;
        iconHost.setAttribute('aria-hidden', 'true');
        root.appendChild(iconHost);
    }

    const title = document.createElement('h3');
    title.className = 'text-sm font-semibold text-slate-200 tracking-tight';
    title.textContent = opts.title;
    root.appendChild(title);

    if (opts.description) {
        const desc = document.createElement('p');
        desc.className = 'text-[12px] text-slate-400 max-w-sm leading-relaxed';
        desc.textContent = opts.description;
        root.appendChild(desc);
    }

    if (opts.action || opts.secondaryAction) {
        const actions = document.createElement('div');
        actions.className = 'flex items-center gap-2 mt-1';
        if (opts.action) actions.appendChild(createButton({ size: 'sm', variant: 'primary', ...opts.action }));
        if (opts.secondaryAction)
            actions.appendChild(createButton({ size: 'sm', variant: 'ghost', ...opts.secondaryAction }));
        root.appendChild(actions);
    }

    return root;
}
