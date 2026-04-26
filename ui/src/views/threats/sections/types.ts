/** Subset of /api/v1/guards/status used by the budget gauge + firewall stats + traffic flow. */
export interface GuardsStatus {
    firewall?: {
        enabled?: boolean;
        disabled_reason?: string | null;
        total_scanned?: number;
        total_blocked?: number;
        block_by_signature?: Record<string, number>;
    };
    budget?: {
        total_cost_today?: number;
        daily_limit?: number;
    };
    /** Per-feature toggle map (injection_guard, link_sanitizer, …). Used by O.4 flow. */
    features?: Record<string, boolean>;
    /** Per-endpoint circuit-breaker state. Used by O.4 flow to color provider nodes. */
    circuit_breakers?: Record<string, { state?: string; failure_count?: number; failure_threshold?: number }>;
}

/** Per-ring latency record from /api/v1/metrics/latency. */
export interface RingLatency {
    p50?: number;
    p95?: number;
    p99?: number;
    count?: number;
}

export interface LatencyMetrics {
    rings?: Record<string, RingLatency | undefined>;
    ttft?: { p50?: number; p95?: number; p99?: number; samples?: number };
}

/** Single trace from /api/v1/metrics/ring-timeline. */
export interface RingTrace {
    timestamp?: number;
    req_id?: string;
    total_ms?: number;
    upstream_ms?: number;
    ttft_ms?: number;
    rings?: Record<string, { duration_ms?: number; plugins?: Array<{ name: string; ms: number }> }>;
}

export interface TimelinePayload {
    traces?: RingTrace[];
}

export const RING_NAMES = ['ingress', 'pre_flight', 'routing', 'post_flight', 'background'] as const;
export type RingName = (typeof RING_NAMES)[number];

/** Tailwind class fragments per ring — kept literal so the content scanner picks them up. */
export const RING_STYLE: Record<RingName, { bar: string; text: string; label: string }> = {
    ingress: { bar: 'bg-rose-500/60', text: 'text-rose-400', label: 'INGRESS' },
    pre_flight: { bar: 'bg-amber-500/60', text: 'text-amber-400', label: 'PRE-FLIGHT' },
    routing: { bar: 'bg-sky-500/60', text: 'text-sky-400', label: 'ROUTING' },
    post_flight: { bar: 'bg-violet-500/60', text: 'text-violet-400', label: 'POST-FLIGHT' },
    background: { bar: 'bg-teal-500/60', text: 'text-teal-400', label: 'BACKGROUND' },
};
