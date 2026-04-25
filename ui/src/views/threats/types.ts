/** Raw counters extracted from /metrics + /health + /api/v1/guards/status. */
export interface ThreatsKpiData {
    requests: number;
    blocked: number;
    piiMasked: number;
    passRatePct: number; // 0-100
    errors: number;
    tokens: number;
    /** Process uptime in seconds, or null if /health was unreachable. */
    uptimeSeconds: number | null;
    /** [healthy, total] endpoint pool counts, or null if /health was unreachable. */
    pool: { healthy: number; total: number } | null;
    /** When set, the upstream that produced the failure — surfaces in tooltips. */
    error?: string;
}

/** A single security event coming through the SSE feed. */
export interface SecurityEvent {
    /** Free-text timestamp from the backend log line; not parsed. */
    timestamp?: string;
    /** Level — INFO, WARNING, ERROR, CRITICAL, SECURITY (case-insensitive). */
    level?: string;
    message?: string;
    /** Optional request id for drilldown. */
    req_id?: string;
    /** Signature / rule that fired, when known. Used for mute keys. */
    signature?: string;
}

export type EventFeedStatus = 'idle' | 'connecting' | 'streaming' | 'error' | 'reconnecting' | 'unauthenticated';
