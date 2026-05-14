/**
 * Story registry for the Storybook-lite gallery.
 *
 * Each primitive contributes a small set of variants we want to keep an eye
 * on — the ones that exercise the loud parts of its API. The gallery groups
 * stories by `primitive` and renders them inside its own card-per-variant
 * layout. Add a story here when you ship a new primitive variant; remove
 * one when the variant is retired so the gallery stays curated, not a dump.
 */
import {
    attachTooltip,
    confirm,
    createBadge,
    createButton,
    createCard,
    createCardHeader,
    createDrawer,
    createEmptyState,
    createErrorState,
    createInput,
    createMetricTile,
    createSparkline,
    createSkeleton,
    createTable,
    createTabs,
    createToggle,
    prompt,
} from '../ui';

export interface Story {
    primitive: string;
    variant: string;
    description?: string;
    render: () => HTMLElement;
}

export const stories: Story[] = [
    // Button — every variant + a few states.
    { primitive: 'Button', variant: 'primary · md', render: () => createButton({ label: 'Save', variant: 'primary' }) },
    { primitive: 'Button', variant: 'secondary · md', render: () => createButton({ label: 'Cancel' }) },
    { primitive: 'Button', variant: 'ghost · md', render: () => createButton({ label: 'Skip', variant: 'ghost' }) },
    {
        primitive: 'Button',
        variant: 'destructive · md',
        render: () => createButton({ label: 'Delete', variant: 'destructive' }),
    },
    {
        primitive: 'Button',
        variant: 'primary · sm',
        render: () => createButton({ label: 'Save', size: 'sm', variant: 'primary' }),
    },
    {
        primitive: 'Button',
        variant: 'primary · lg',
        render: () => createButton({ label: 'Save', size: 'lg', variant: 'primary' }),
    },
    {
        primitive: 'Button',
        variant: 'disabled',
        render: () => createButton({ label: 'Cannot click', disabled: true, variant: 'primary' }),
    },
    {
        primitive: 'Button',
        variant: 'pressed (toggle)',
        render: () => createButton({ label: 'Muted', pressed: true, size: 'sm' }),
    },
    {
        primitive: 'Button',
        variant: 'with leading icon',
        render: () =>
            createButton({
                label: 'Inspect',
                size: 'sm',
                variant: 'ghost',
                icon: '<svg viewBox="0 0 16 16" width="11" height="11" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><circle cx="7" cy="7" r="4.5"/><path d="M10.4 10.4 L13.5 13.5"/></svg>',
            }),
    },

    // Badge — every intent + dot variant.
    ...(['neutral', 'primary', 'success', 'warning', 'danger', 'info'] as const).map((intent) => ({
        primitive: 'Badge',
        variant: `intent: ${intent}`,
        render: () => createBadge({ label: intent.toUpperCase(), intent }),
    })),
    {
        primitive: 'Badge',
        variant: 'with status dot',
        render: () => createBadge({ label: 'live', intent: 'success', dot: true }),
    },
    { primitive: 'Badge', variant: 'sm', render: () => createBadge({ label: 'tag', size: 'sm' }) },
    { primitive: 'Badge', variant: 'md', render: () => createBadge({ label: 'tag', size: 'md' }) },

    // Card — flat, raised, interactive, with header.
    {
        primitive: 'Card',
        variant: 'flat · body only',
        render: () => createCard({ body: 'Plain card. The lowest-emphasis container we ship.' }),
    },
    {
        primitive: 'Card',
        variant: 'raised · header + body + footer',
        render: () => {
            const footer = document.createElement('div');
            footer.className = 'flex items-center justify-end gap-2 p-3 border-t border-white/[0.04]';
            footer.appendChild(createButton({ label: 'Cancel', size: 'sm', variant: 'ghost' }));
            footer.appendChild(createButton({ label: 'Save', size: 'sm', variant: 'primary' }));
            return createCard({
                elevation: 'raised',
                header: createCardHeader('Endpoint', 'openai · gpt-4o-mini'),
                body: 'Body content can be a string, a node, or an array of nodes.',
                footer,
            });
        },
    },
    {
        primitive: 'Card',
        variant: 'interactive (Enter/Space activates)',
        render: () =>
            createCard({
                interactive: true,
                onClick: () => {},
                body: 'Click or press Enter / Space — keyboard-accessible by design.',
            }),
    },

    // EmptyState
    {
        primitive: 'EmptyState',
        variant: 'no events yet',
        render: () =>
            createEmptyState({
                title: 'No security events yet',
                description: 'When the WAF or guards block a request it lands here in real time.',
            }),
    },
    {
        primitive: 'EmptyState',
        variant: 'with primary + secondary CTA',
        render: () =>
            createEmptyState({
                title: 'No endpoints configured',
                description: 'Wire up at least one provider to start routing requests.',
                action: { label: 'Add endpoint' },
                secondaryAction: { label: 'Read docs' },
            }),
    },

    // ErrorState
    {
        primitive: 'ErrorState',
        variant: 'with retry + collapsed detail',
        render: () =>
            createErrorState({
                title: 'Failed to load metrics',
                description: 'The /metrics endpoint did not respond.',
                detail: 'TypeError: Failed to fetch\n    at api.fetchMetrics (api.js:42)',
                onRetry: () => {},
            }),
    },

    // Skeleton
    { primitive: 'Skeleton', variant: 'line', render: () => createSkeleton({ width: '60%' }) },
    {
        primitive: 'Skeleton',
        variant: 'block',
        render: () => createSkeleton({ shape: 'block', height: '4rem' }),
    },
    {
        primitive: 'Skeleton',
        variant: 'circle (40px)',
        render: () => createSkeleton({ shape: 'circle', width: '2.5rem', height: '2.5rem' }),
    },
    {
        primitive: 'Skeleton',
        variant: 'list (repeat=4)',
        render: () => createSkeleton({ repeat: 4, gap: 'gap-2' }),
    },

    // MetricTile
    {
        primitive: 'MetricTile',
        variant: 'neutral · md',
        render: () => createMetricTile({ label: 'Requests Today', value: '1,234' }),
    },
    {
        primitive: 'MetricTile',
        variant: 'with provenance ℹ',
        render: () =>
            createMetricTile({
                label: 'Threats Blocked',
                value: '7',
                intent: 'primary',
                provenance:
                    'Sum of llm_proxy_injection_blocked_total + llm_proxy_auth_failures_total. Window: since boot.',
            }),
    },
    {
        primitive: 'MetricTile',
        variant: 'loading',
        render: () => createMetricTile({ label: 'Pass Rate', value: '', loading: true, intent: 'success' }),
    },
    {
        primitive: 'MetricTile',
        variant: 'error',
        render: () =>
            createMetricTile({ label: 'Errors', value: '', error: '/metrics returned 502', intent: 'danger' }),
    },
    {
        primitive: 'MetricTile',
        variant: 'sm · with sub line',
        render: () =>
            createMetricTile({
                label: 'Uptime',
                value: '12h 04m',
                sub: 'since 2026-04-25 03:55 UTC',
                intent: 'info',
                size: 'sm',
            }),
    },
    {
        primitive: 'MetricTile',
        variant: 'with sparkline (24h trend)',
        description:
            'Pass {sparkline: { data: number[] }} to overlay a 24-point trend strip below the value. ' +
            'The strip color tracks the tile intent unless overridden.',
        render: () =>
            createMetricTile({
                label: 'Total Spend',
                value: '$12.43',
                sub: 'last 24h',
                intent: 'success',
                sparkline: {
                    data: [
                        0.1, 0.3, 0.2, 0.5, 0.8, 1.2, 1.6, 2.0, 2.4, 2.7, 3.1, 3.4, 3.6, 3.9, 4.5, 5.1, 5.8, 6.5, 7.4,
                        8.6, 9.7, 10.9, 11.8, 12.4,
                    ],
                },
            }),
    },
    {
        primitive: 'MetricTile',
        variant: 'sparkline · flat series',
        description: 'Constant data sits on the mid-line, not pinned to top — empty surfaces look intentional.',
        render: () =>
            createMetricTile({
                label: 'Errors',
                value: '0',
                intent: 'success',
                sparkline: { data: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0] },
            }),
    },
    {
        primitive: 'Sparkline',
        variant: 'standalone, no area',
        description: 'Bare line, no gradient — useful inside dense rows where a fill would be visual noise.',
        render: () => {
            const wrap = document.createElement('div');
            wrap.style.width = '240px';
            wrap.appendChild(
                createSparkline({
                    data: [10, 12, 9, 15, 18, 14, 11, 16, 22, 19, 24, 28],
                    color: 'rose',
                    area: false,
                    height: 24,
                })
            );
            return wrap;
        },
    },

    // Modal — interactive samples; click the button to see it open.
    {
        primitive: 'Modal',
        variant: 'confirm()',
        description: 'Click to open a yes/no modal. Resolves the promise with true/false.',
        render: () =>
            createButton({
                label: 'Open confirm',
                size: 'sm',
                onClick: async () => {
                    const ok = await confirm({
                        title: 'Restart proxy?',
                        message: 'In-flight requests finish, then the proxy restarts.',
                    });
                    console.log('[gallery] confirm result =', ok);
                },
            }),
    },
    {
        primitive: 'Modal',
        variant: 'confirm() · danger',
        description: 'Destructive variant — rose-coloured panel border + primary button.',
        render: () =>
            createButton({
                label: 'Open destructive',
                size: 'sm',
                variant: 'destructive',
                onClick: async () => {
                    const ok = await confirm({
                        title: 'Delete endpoint',
                        message: 'Remove "openai-mini" from the registry? Traffic falls back through the chain.',
                        confirmLabel: 'Delete',
                        danger: true,
                    });
                    console.log('[gallery] danger confirm =', ok);
                },
            }),
    },
    {
        primitive: 'Modal',
        variant: 'prompt() · password',
        description: 'Single-line input with focus trap and Enter-to-confirm.',
        render: () =>
            createButton({
                label: 'Open prompt',
                size: 'sm',
                onClick: async () => {
                    const value = await prompt({
                        title: 'API key',
                        label: 'Bearer token',
                        inputType: 'password',
                        placeholder: 'sk-…',
                        validate: (v) => (v.startsWith('sk-') ? null : 'Token must start with "sk-".'),
                    });
                    console.log('[gallery] prompt result =', value);
                },
            }),
    },

    // Drawer — non-modal investigation surface (single-drawer model).
    {
        primitive: 'Drawer',
        variant: 'open with body string',
        description: 'Slides in from the right; Escape / backdrop / ✕ close.',
        render: () =>
            createButton({
                label: 'Open drawer',
                size: 'sm',
                onClick: () => {
                    createDrawer({
                        title: 'Endpoint · openai-mini',
                        body: '<p>Drawer body. Replace with rich nodes for tables and code blocks.</p>',
                    });
                },
            }),
    },
    {
        primitive: 'Drawer',
        variant: 'setBody after open (async fetch)',
        description: 'Drawer opens with a skeleton, body is replaced once the fetch resolves.',
        render: () =>
            createButton({
                label: 'Simulate fetch',
                size: 'sm',
                variant: 'primary',
                onClick: () => {
                    const skeleton = createSkeleton({ shape: 'block', height: '5rem', repeat: 3, gap: 'gap-3' });
                    const handle = createDrawer({ title: 'Plugin · smart_router', body: skeleton, width: 560 });
                    setTimeout(() => {
                        if (!handle.isOpen) return;
                        const body = document.createElement('div');
                        body.innerHTML =
                            '<p class="text-emerald-300">Loaded.</p><pre class="mt-3 p-3 rounded-md bg-black/40 text-[11px] font-mono text-slate-300">{\n  "ring": "routing",\n  "enabled": true\n}</pre>';
                        handle.setBody(body);
                    }, 1_500);
                },
            }),
    },

    // Tooltip
    {
        primitive: 'Tooltip',
        variant: 'neutral · top placement',
        description: 'Hover or focus to show. 200ms delay on mouse, immediate on focus.',
        render: () => {
            const btn = createButton({ label: 'Hover or tab to me', size: 'sm', variant: 'ghost' });
            attachTooltip(btn, { content: 'I appear after a short hover delay, immediately on keyboard focus.' });
            return btn;
        },
    },
    {
        primitive: 'Tooltip',
        variant: 'danger intent',
        description: 'Used to flag warnings (rose tint) without changing the trigger.',
        render: () => {
            const badge = createBadge({ label: 'EXPERIMENTAL', intent: 'warning' });
            attachTooltip(badge as HTMLElement, {
                content: 'Behavior may change between minor versions until ZT mode is GA.',
                intent: 'danger',
            });
            // Badges aren't naturally focusable — wrap in a focusable button for keyboard users.
            const wrap = document.createElement('button');
            wrap.type = 'button';
            wrap.className = 'inline-flex';
            wrap.appendChild(badge);
            attachTooltip(wrap, {
                content: 'Behavior may change between minor versions until ZT mode is GA.',
                intent: 'danger',
            });
            return wrap;
        },
    },

    // Input / FormField
    {
        primitive: 'Input',
        variant: 'with label + help',
        render: () =>
            createInput({
                name: 'demo-name',
                label: 'Endpoint id',
                helpText: 'letters, digits, - or _',
                placeholder: 'my-openai',
            }).root,
    },
    {
        primitive: 'Input',
        variant: 'required · with error',
        render: () =>
            createInput({ name: 'demo-required', label: 'Base URL', required: true, error: 'Required.' }).root,
    },
    {
        primitive: 'Input',
        variant: 'password · autoComplete',
        render: () =>
            createInput({
                name: 'demo-pwd',
                label: 'API key',
                type: 'password',
                placeholder: 'sk-…',
                autoComplete: 'new-password',
                helpText: 'Pasted in cleartext — keep this tab private.',
            }).root,
    },

    // Toggle
    {
        primitive: 'Toggle',
        variant: 'unchecked',
        render: () => createToggle({ label: 'Block prompt injection', description: 'Ring 1 · pre-flight' }).root,
    },
    {
        primitive: 'Toggle',
        variant: 'checked',
        render: () => createToggle({ label: 'PII redaction', checked: true }).root,
    },
    {
        primitive: 'Toggle',
        variant: 'disabled',
        render: () =>
            createToggle({ label: 'Zero-trust mode', description: 'Requires plan upgrade', disabled: true }).root,
    },

    // Table
    {
        primitive: 'Table',
        variant: 'sortable · with renderer',
        description: 'Click "Requests" to sort. Health uses a custom renderer.',
        render: () =>
            createTable<{ id: string; requests: number; healthy: boolean }>({
                columns: [
                    { key: 'id', label: 'Endpoint', sortable: true },
                    { key: 'requests', label: 'Requests', align: 'right', sortable: true },
                    {
                        key: 'healthy',
                        label: 'Health',
                        render: (row) => {
                            const span = document.createElement('span');
                            span.className = row.healthy ? 'text-emerald-300' : 'text-rose-300';
                            span.textContent = row.healthy ? 'OK' : 'DEAD';
                            return span;
                        },
                    },
                ],
                rows: [
                    { id: 'openai-mini', requests: 1240, healthy: true },
                    { id: 'anthropic', requests: 8731, healthy: true },
                    { id: 'ollama-local', requests: 17, healthy: false },
                ],
            }).root,
    },
    {
        primitive: 'Table',
        variant: 'empty state',
        render: () => {
            const empty = document.createElement('p');
            empty.textContent = 'No endpoints registered yet.';
            empty.className = 'text-slate-500 text-[11px] font-mono';
            return createTable({ columns: [{ key: 'x', label: 'Empty' }], rows: [], emptyState: empty }).root;
        },
    },

    // Tabs
    {
        primitive: 'Tabs',
        variant: 'three panes · lazy render',
        description: 'Arrow keys + Home/End walk the list. Each pane renders the first time it is activated.',
        render: () =>
            createTabs({
                tabs: [
                    {
                        id: 'overview',
                        label: 'Overview',
                        render: () => {
                            const p = document.createElement('p');
                            p.className = 'text-[11px] text-slate-300';
                            p.textContent = 'Overview pane content. Cheap to render.';
                            return p;
                        },
                    },
                    {
                        id: 'timeline',
                        label: 'Timeline',
                        badge: { label: '3', intent: 'primary' },
                        render: () => {
                            const p = document.createElement('p');
                            p.className = 'text-[11px] text-slate-300';
                            p.textContent = 'Timeline pane (rendered lazily on first click).';
                            return p;
                        },
                    },
                    {
                        id: 'config',
                        label: 'Config',
                        render: () => {
                            const pre = document.createElement('pre');
                            pre.className = 'text-[10px] font-mono text-slate-300 bg-black/40 rounded-md p-3';
                            pre.textContent = '{ "ring": "routing", "enabled": true }';
                            return pre;
                        },
                    },
                ],
            }).root,
    },
];
