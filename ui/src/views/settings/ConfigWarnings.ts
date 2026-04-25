/**
 * Config warnings widget — surfaces the list of startup-validation
 * warnings produced by `core/startup_checks.py` to the operator. Empty
 * list ⇒ green "All checks passed" badge. Non-empty ⇒ a row per warning
 * with the actionable message.
 *
 * Mounts above the rest of the Settings sections so config drift is
 * visible the moment the operator opens the tab.
 */
import { createBadge, createCard, createErrorState, createSkeleton, cx } from '../../ui';

export interface ConfigWarningsApi {
    fetchConfigWarnings: () => Promise<{ warnings?: string[] } | null>;
}

export function mountConfigWarnings(host: HTMLElement, api: ConfigWarningsApi): () => Promise<void> {
    const heading = document.createElement('div');
    heading.className = 'flex items-center justify-between mb-4';
    const title = document.createElement('h2');
    title.className = 'text-xs font-bold text-white';
    title.textContent = 'Config Warnings';
    heading.appendChild(title);
    const statusHost = document.createElement('div');
    heading.appendChild(statusHost);

    const inner = document.createElement('div');
    inner.appendChild(createSkeleton({ shape: 'block', height: '2rem', ariaLabel: '' }));

    const body = document.createElement('div');
    body.appendChild(heading);
    body.appendChild(inner);

    host.replaceChildren(createCard({ body, testId: 'settings-config-warnings' }));

    function renderEmpty(): void {
        statusHost.replaceChildren(
            createBadge({
                label: 'all checks passed',
                intent: 'success',
                dot: true,
                size: 'sm',
                testId: 'config-warnings-ok',
            })
        );
        const empty = document.createElement('p');
        empty.className = 'text-[10px] text-slate-500 font-mono';
        empty.textContent = 'No warnings — startup validation passed cleanly.';
        empty.setAttribute('data-testid', 'config-warnings-empty');
        inner.replaceChildren(empty);
    }

    function renderWarnings(warnings: string[]): void {
        statusHost.replaceChildren(
            createBadge({
                label: `${warnings.length} warning${warnings.length === 1 ? '' : 's'}`,
                intent: 'warning',
                dot: true,
                size: 'sm',
                testId: 'config-warnings-count',
            })
        );
        const list = document.createElement('ul');
        list.className = 'space-y-2';
        list.setAttribute('data-testid', 'config-warnings-list');
        for (const w of warnings) {
            const li = document.createElement('li');
            li.className = cx(
                'flex items-start gap-2 p-3 rounded-lg border border-amber-500/20 bg-amber-500/[0.04]',
                'text-[10px] font-mono text-amber-200/90 leading-relaxed whitespace-pre-wrap'
            );
            li.setAttribute('role', 'alert');
            const icon = document.createElement('span');
            icon.className = 'shrink-0 mt-0.5 text-amber-400';
            icon.setAttribute('aria-hidden', 'true');
            icon.textContent = '⚠';
            li.appendChild(icon);
            const text = document.createElement('span');
            text.textContent = w;
            li.appendChild(text);
            list.appendChild(li);
        }
        inner.replaceChildren(list);
    }

    async function refresh(): Promise<void> {
        try {
            const data = await api.fetchConfigWarnings();
            const warnings = (data?.warnings ?? []).filter((w): w is string => typeof w === 'string' && w.length > 0);
            if (warnings.length === 0) renderEmpty();
            else renderWarnings(warnings);
        } catch (err) {
            statusHost.replaceChildren();
            inner.replaceChildren(
                createErrorState({
                    title: 'Could not read /api/v1/config/warnings',
                    description: 'The startup-checks endpoint did not respond.',
                    detail: (err as Error)?.message,
                    onRetry: () => void refresh(),
                    testId: 'config-warnings-error',
                })
            );
        }
    }

    void refresh();
    return refresh;
}
