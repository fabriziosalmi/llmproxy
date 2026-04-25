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
    createBadge,
    createButton,
    createCard,
    createCardHeader,
    createEmptyState,
    createErrorState,
    createMetricTile,
    createSkeleton,
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
];
