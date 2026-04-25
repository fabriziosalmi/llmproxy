import type { BadgeIntent } from '../../ui';

export type RingHook = 'ingress' | 'pre_flight' | 'routing' | 'post_flight' | 'background';

export interface UiSchemaField {
    key: string;
    label?: string;
    default?: unknown;
}

export interface Plugin {
    name: string;
    hook?: RingHook | string;
    entrypoint?: string;
    type?: string;
    description?: string;
    enabled?: boolean;
    timeout_ms?: number;
    fail_policy?: 'open' | 'closed' | string;
    version?: string;
    /** Optional ui-schema published by the plugin describing its config knobs. */
    ui_schema?: UiSchemaField[];
}

export interface PluginStats {
    invocations?: number;
    errors?: number;
    blocks?: number;
    avg_latency_ms?: number;
    latency_percentiles?: { p50?: number; p95?: number; p99?: number };
}

/** Map of plugin name → its stats record. */
export type PluginStatsMap = Record<string, PluginStats | undefined>;

/** POST /api/v1/plugins/install payload. */
export interface InstallPluginInput {
    name: string;
    hook: RingHook | string;
    entrypoint: string;
    type: 'python' | 'wasm';
    timeout_ms: number;
    fail_policy: 'open' | 'closed';
    description: string;
    enabled: boolean;
}

export const RING_OPTIONS: Array<{ value: RingHook; label: string }> = [
    { value: 'ingress', label: 'ingress (Ring 1)' },
    { value: 'pre_flight', label: 'pre_flight (Ring 2)' },
    { value: 'routing', label: 'routing (Ring 3)' },
    { value: 'post_flight', label: 'post_flight (Ring 4)' },
    { value: 'background', label: 'background (Ring 5)' },
];

/** Closest Badge intent for each ring — used for the ring badge on each card. */
export const RING_INTENT: Record<RingHook, BadgeIntent> = {
    ingress: 'primary',
    pre_flight: 'warning',
    routing: 'info',
    post_flight: 'primary',
    background: 'success',
};

export function ringLabel(ring: string | undefined): string {
    if (!ring) return 'UNKNOWN';
    return ring.toUpperCase().replace(/_/g, '-');
}

export function ringIntent(ring: string | undefined): BadgeIntent {
    if (!ring) return 'neutral';
    const k = ring.toLowerCase() as RingHook;
    return RING_INTENT[k] ?? 'neutral';
}
