import { cx } from './classnames';
import { createButton, type ButtonOptions } from './Button';

export interface ErrorStateOptions {
    /** Short, blame-free description of what failed. */
    title: string;
    /** Plain-language reason. Optionally surfaces the upstream message. */
    description?: string;
    /** Raw error payload — rendered in a collapsed mono block for ops to copy/paste. */
    detail?: string;
    /** Primary action — usually "Retry". */
    onRetry?: () => void;
    retryLabel?: string;
    /** Optional secondary action — e.g. "Open docs" or "Open status page". */
    secondaryAction?: ButtonOptions;
    className?: string;
    testId?: string;
}

const ERROR_ICON =
    '<svg viewBox="0 0 24 24" width="32" height="32" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">' +
    '<circle cx="12" cy="12" r="9"/><path d="M12 7v6"/><circle cx="12" cy="16" r="0.5" fill="currentColor"/></svg>';

export function createErrorState(opts: ErrorStateOptions): HTMLElement {
    const root = document.createElement('div');
    root.className = cx(
        'flex flex-col items-center justify-center text-center gap-3 px-6 py-10',
        'rounded-xl border border-red-500/20 bg-red-500/[0.04]',
        opts.className
    );
    root.setAttribute('role', 'alert');
    root.setAttribute('aria-live', 'polite');
    if (opts.testId) root.setAttribute('data-testid', opts.testId);

    const iconHost = document.createElement('div');
    iconHost.className = 'w-8 h-8 flex items-center justify-center text-red-400';
    iconHost.innerHTML = ERROR_ICON;
    root.appendChild(iconHost);

    const title = document.createElement('h3');
    title.className = 'text-sm font-semibold text-red-200 tracking-tight';
    title.textContent = opts.title;
    root.appendChild(title);

    if (opts.description) {
        const desc = document.createElement('p');
        desc.className = 'text-[12px] text-slate-400 max-w-sm leading-relaxed';
        desc.textContent = opts.description;
        root.appendChild(desc);
    }

    if (opts.detail) {
        const details = document.createElement('details');
        details.className = 'w-full max-w-sm mt-1';

        const summary = document.createElement('summary');
        summary.className = 'text-[11px] text-slate-500 cursor-pointer select-none';
        summary.textContent = 'Show details';
        details.appendChild(summary);

        const pre = document.createElement('pre');
        pre.className =
            'mt-2 p-2 rounded-md bg-black/40 border border-white/[0.04] text-[10px] text-slate-300 ' +
            'font-mono whitespace-pre-wrap break-words text-left';
        pre.textContent = opts.detail;
        details.appendChild(pre);

        root.appendChild(details);
    }

    if (opts.onRetry || opts.secondaryAction) {
        const actions = document.createElement('div');
        actions.className = 'flex items-center gap-2 mt-1';
        if (opts.onRetry) {
            actions.appendChild(
                createButton({
                    label: opts.retryLabel ?? 'Retry',
                    variant: 'primary',
                    size: 'sm',
                    onClick: opts.onRetry,
                    testId: 'error-state-retry',
                })
            );
        }
        if (opts.secondaryAction)
            actions.appendChild(createButton({ size: 'sm', variant: 'ghost', ...opts.secondaryAction }));
        root.appendChild(actions);
    }

    return root;
}
