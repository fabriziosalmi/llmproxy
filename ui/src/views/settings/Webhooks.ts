import {
    createBadge,
    createButton,
    createCard,
    createEmptyState,
    createErrorState,
    createSkeleton,
    cx,
} from '../../ui';
import type { BadgeIntent } from '../../ui';
import type { WebhooksConfig } from './types';

export interface WebhooksApi {
    fetchWebhooks: () => Promise<WebhooksConfig>;
    testWebhook: () => Promise<unknown>;
}

const TARGET_INTENT: Record<string, BadgeIntent> = {
    slack: 'primary',
    teams: 'info',
    discord: 'primary',
    generic: 'neutral',
};

function endpointRow(name: string, target: string, events: string[]): HTMLElement {
    const row = document.createElement('div');
    row.className = 'flex items-center justify-between p-2 bg-white/[0.02] rounded-lg';
    const left = document.createElement('div');
    left.className = 'flex items-center gap-2';
    left.appendChild(
        createBadge({
            label: target.toUpperCase(),
            intent: TARGET_INTENT[target] ?? 'neutral',
            size: 'sm',
            testId: `webhook-target-${name}`,
        })
    );
    const nameSpan = document.createElement('span');
    nameSpan.className = 'text-[10px] font-bold text-white';
    nameSpan.textContent = name;
    left.appendChild(nameSpan);
    row.appendChild(left);
    const eventsSpan = document.createElement('span');
    eventsSpan.className = 'text-[10px] font-mono text-slate-500';
    eventsSpan.textContent = events.join(', ');
    row.appendChild(eventsSpan);
    return row;
}

export interface WebhooksHandle {
    refresh: () => Promise<void>;
}

export function mountWebhooks(
    host: HTMLElement,
    api: WebhooksApi,
    toast?: (m: string, k?: 'success' | 'error' | 'warning' | 'info') => void
): WebhooksHandle {
    const heading = document.createElement('h2');
    heading.className = 'text-xs font-bold text-white';
    heading.textContent = 'Webhooks';

    const testBtn = createButton({ label: 'Test Fire', size: 'sm', variant: 'ghost', testId: 'test-webhook-btn' });
    testBtn.classList.add('text-violet-400', 'hover:text-violet-300');
    testBtn.addEventListener('click', async () => {
        const btn = testBtn as HTMLButtonElement;
        btn.disabled = true;
        const labelSpan = btn.querySelector('span:last-child');
        const original = labelSpan?.textContent ?? 'Test Fire';
        if (labelSpan) labelSpan.textContent = 'Sending…';
        try {
            await api.testWebhook();
            toast?.('Test webhook dispatched', 'success');
        } catch (err) {
            toast?.(`Webhook test failed: ${(err as Error)?.message ?? err}`, 'error');
        } finally {
            btn.disabled = false;
            if (labelSpan) labelSpan.textContent = original;
        }
    });

    const headerRow = document.createElement('div');
    headerRow.className = 'flex items-center justify-between mb-4';
    headerRow.appendChild(heading);
    headerRow.appendChild(testBtn);

    const inner = document.createElement('div');
    inner.appendChild(createSkeleton({ shape: 'block', height: '5rem', ariaLabel: '' }));

    const body = document.createElement('div');
    body.appendChild(headerRow);
    body.appendChild(inner);

    host.replaceChildren(createCard({ body, testId: 'settings-webhooks' }));

    async function refresh(): Promise<void> {
        try {
            const data = await api.fetchWebhooks();
            if (!data.enabled) {
                inner.replaceChildren(
                    createEmptyState({
                        title: 'Webhooks disabled',
                        description: 'Set security.webhooks.enabled=true in config.yaml to use this surface.',
                        testId: 'webhooks-disabled',
                    })
                );
                return;
            }
            const eps = data.endpoints ?? [];
            const wrap = document.createElement('div');
            const list = document.createElement('div');
            list.className = 'space-y-2';
            list.setAttribute('data-testid', 'webhooks-list');
            if (eps.length === 0) {
                const p = document.createElement('p');
                p.className = 'text-[10px] text-slate-600 font-mono';
                p.textContent = 'No endpoints configured';
                list.appendChild(p);
            } else {
                for (const ep of eps) list.appendChild(endpointRow(ep.name, ep.target, ep.events));
            }
            wrap.appendChild(list);

            const eventTypes = data.event_types ?? [];
            if (eventTypes.length > 0) {
                const tail = document.createElement('div');
                tail.className = 'mt-3 pt-2 border-t border-white/[0.04]';
                const head = document.createElement('p');
                head.className = 'text-[10px] text-slate-600 uppercase font-bold mb-1';
                head.textContent = 'Available Events';
                tail.appendChild(head);
                const chips = document.createElement('div');
                chips.className = cx('flex flex-wrap gap-1');
                for (const e of eventTypes) {
                    const chip = document.createElement('span');
                    chip.className = 'text-[9px] font-mono text-slate-500 bg-white/[0.03] px-1.5 py-0.5 rounded';
                    chip.textContent = e;
                    chips.appendChild(chip);
                }
                tail.appendChild(chips);
                wrap.appendChild(tail);
            }
            inner.replaceChildren(wrap);
        } catch (err) {
            inner.replaceChildren(
                createErrorState({
                    title: 'Webhook service unavailable',
                    description: 'Could not load /api/v1/webhooks.',
                    detail: (err as Error)?.message,
                    onRetry: () => void refresh(),
                    testId: 'webhooks-error',
                })
            );
        }
    }

    void refresh();
    return { refresh };
}
