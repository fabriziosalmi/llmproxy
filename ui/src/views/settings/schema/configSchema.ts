/**
 * Guided Configuration schema — the single, self-documenting registry of the
 * scalar config.yaml knobs that a non-expert should be able to change safely
 * from the UI, each with a plain-language description ("what does this do?").
 *
 * Scope (agreed): scalar / simple-typed, hot-reloadable operational knobs only.
 * Deliberately excluded and left to the raw "Advanced" editor + their own views:
 *   - Structural sections: endpoints, providers, model_groups, fallback_chains,
 *     model_aliases (endpoints already have a dedicated registry + Add form).
 *   - Bind-once-at-startup / self-lockout-risk knobs: server.host/port, TLS,
 *     server.auth.enabled, admin_auth/JWT — a bad flip from a web UI could lock
 *     the operator out, and they need a full restart anyway.
 *
 * Every `path` here is a dotted path into config.yaml verified against the
 * shipped default. `default` documents the shipped value (shown as a hint and
 * used by "reset"). `restartRequired` marks the few knobs that the 30s config
 * hot-reload does NOT fully apply.
 */
import type { Scalar } from './yamlEdit';

export type FieldType = 'boolean' | 'number' | 'enum' | 'stringList' | 'string';

export interface ConfigField {
    path: string;
    label: string;
    /** The inline "aiuto" — one or two plain sentences a non-expert can act on. */
    help: string;
    type: FieldType;
    default?: Scalar;
    /** enum options (value === the YAML token written). */
    options?: Array<{ value: string; label: string }>;
    min?: number;
    max?: number;
    step?: number;
    placeholder?: string;
    /** Hidden until "Show advanced" is toggled. */
    advanced?: boolean;
    /** The 30s hot-reload does not fully apply this — a restart is needed. */
    restartRequired?: boolean;
}

export interface ConfigSection {
    id: string;
    title: string;
    description: string;
    fields: ConfigField[];
}

export const CONFIG_SECTIONS: ConfigSection[] = [
    {
        id: 'shield',
        title: 'Security Shield',
        description: 'The LLM firewall that inspects every prompt and response. Changes apply within ~30s, no restart.',
        fields: [
            {
                path: 'security.enabled',
                label: 'Security shield',
                help: 'Master switch for all prompt-injection, PII, link and response inspection. Turning this off disables the whole shield — leave it on in production.',
                type: 'boolean',
                default: true,
            },
            {
                path: 'security.max_payload_size_kb',
                label: 'Max request size (KB)',
                help: 'Reject any request body larger than this. Guards against payload-flooding denial-of-service. 512 KB fits normal chats comfortably.',
                type: 'number',
                default: 512,
                min: 1,
                max: 102400,
            },
            {
                path: 'security.max_messages',
                label: 'Max messages per request',
                help: 'Maximum chat messages allowed in one request. Caps "conversation-stuffing" attacks that try to bury an injection in a huge history.',
                type: 'number',
                default: 50,
                min: 1,
                max: 1000,
            },
            {
                path: 'security.confidence.regex_escalate_floor',
                label: 'Regex escalation floor',
                help: 'When a single regex rule fires with a score at or above this, the request is escalated to AI adjudication instead of passing unreviewed. Lower = stricter (more escalations).',
                type: 'number',
                default: 0.6,
                min: 0,
                max: 1,
                step: 0.05,
            },
        ],
    },
    {
        id: 'links',
        title: 'Link & Homograph Protection',
        description: 'Blocks unsafe and look-alike (IDN homograph) URLs in prompts and model responses.',
        fields: [
            {
                path: 'security.link_sanitization.enabled',
                label: 'Link sanitization',
                help: 'Scan every URL in prompts and responses. Off disables blocklist, risk scoring and homograph checks below.',
                type: 'boolean',
                default: true,
            },
            {
                path: 'security.link_sanitization.blocked_domains',
                label: 'Blocked domains',
                help: 'Exact domains (and their subdomains) to always block, e.g. malicious-site.com. One entry per chip.',
                type: 'stringList',
                default: [],
                placeholder: 'malicious-site.com',
            },
            {
                path: 'security.link_sanitization.homograph_protection.enabled',
                label: 'Homograph protection',
                help: 'Catch Punycode / Cyrillic-Greek look-alikes of your brands (e.g. a fake "pаypal.com" with a Cyrillic а). No effect until you add brands below.',
                type: 'boolean',
                default: true,
            },
            {
                path: 'security.link_sanitization.homograph_protection.brands',
                label: 'Protected brands',
                help: 'Registrable domains to protect from look-alikes, e.g. paypal.com, bankofamerica.com. A non-ASCII host whose skeleton matches one of these — but is not the real domain — is flagged.',
                type: 'stringList',
                default: [],
                placeholder: 'paypal.com',
            },
            {
                path: 'security.link_sanitization.homograph_protection.log_only',
                label: 'Homograph: log only',
                help: 'Recommended for the first days: log look-alike hits without blocking, so you can confirm zero false positives on real traffic before enforcing.',
                type: 'boolean',
                default: false,
            },
            {
                path: 'security.link_sanitization.risk_scoring.enabled',
                label: 'Lexical risk scoring',
                help: 'Opt-in heuristic that scores each host on network-free signals (risky TLD, phishing keywords, IP-as-host, excess hyphens/digits). Augments the blocklist. Fail-open.',
                type: 'boolean',
                default: false,
                advanced: true,
            },
            {
                path: 'security.link_sanitization.risk_scoring.block_threshold',
                label: 'Risk block threshold',
                help: 'Block hosts scoring at or above this (0..1). Lower = stricter; 0.5 also catches risky-TLD DGAs.',
                type: 'number',
                default: 0.7,
                min: 0,
                max: 1,
                step: 0.05,
                advanced: true,
            },
            {
                path: 'security.link_sanitization.risk_scoring.log_only',
                label: 'Risk scoring: log only',
                help: 'Log the computed risk but never block or replace the link. Use to observe before enforcing.',
                type: 'boolean',
                default: false,
                advanced: true,
            },
        ],
    },
    {
        id: 'ledger',
        title: 'Threat Ledger',
        description: 'Cross-session threat intelligence — flags actors whose recent requests trend malicious.',
        fields: [
            {
                path: 'security.threat_ledger.enabled',
                label: 'Threat ledger',
                help: 'Track per-actor threat scores across requests within a rolling window. Off disables cross-session correlation.',
                type: 'boolean',
                default: true,
                advanced: true,
            },
            {
                path: 'security.threat_ledger.threshold',
                label: 'Actor threat threshold',
                help: 'Cumulative score at which an actor is treated as hostile. Higher = more tolerant.',
                type: 'number',
                default: 3.0,
                min: 0.5,
                max: 100,
                step: 0.5,
                advanced: true,
            },
            {
                path: 'security.threat_ledger.window_seconds',
                label: 'Ledger window (seconds)',
                help: 'How long an actor’s events are remembered when computing their trend. 600 = 10 minutes.',
                type: 'number',
                default: 600,
                min: 30,
                max: 86400,
                advanced: true,
            },
            {
                path: 'security.threat_ledger.min_events',
                label: 'Minimum events',
                help: 'Don’t judge an actor until they’ve produced at least this many events — avoids punishing a single unlucky request.',
                type: 'number',
                default: 3,
                min: 1,
                max: 100,
                advanced: true,
            },
        ],
    },
    {
        id: 'caching',
        title: 'Caching',
        description: 'Response cache to cut latency and cost on repeated prompts.',
        fields: [
            {
                path: 'caching.enabled',
                label: 'Response caching',
                help: 'Serve identical repeated requests from cache instead of re-calling the model.',
                type: 'boolean',
                default: true,
            },
            {
                path: 'caching.ttl',
                label: 'Cache TTL (seconds)',
                help: 'How long a cached response stays fresh. 3600 = 1 hour.',
                type: 'number',
                default: 3600,
                min: 0,
                max: 604800,
            },
            {
                path: 'caching.negative_cache.ttl',
                label: 'Negative cache TTL (seconds)',
                help: 'How long failed/empty results are remembered so a broken upstream isn’t hammered. 300 = 5 minutes.',
                type: 'number',
                default: 300,
                min: 0,
                max: 86400,
                advanced: true,
            },
            {
                path: 'caching.negative_cache.maxsize',
                label: 'Negative cache size',
                help: 'Maximum number of negative entries kept in memory before eviction.',
                type: 'number',
                default: 50000,
                min: 0,
                max: 10000000,
                advanced: true,
            },
        ],
    },
    {
        id: 'budget',
        title: 'Budget',
        description: 'Daily spend guardrails.',
        fields: [
            {
                path: 'budget.daily_limit',
                label: 'Daily hard limit (USD)',
                help: 'Hard ceiling on spend per day. At the limit, requests are refused (or routed to local, below).',
                type: 'number',
                default: 50,
                min: 0,
                max: 1000000,
                step: 0.5,
            },
            {
                path: 'budget.soft_limit',
                label: 'Soft warning limit (USD)',
                help: 'Fire a webhook warning when daily spend crosses this. Set below the hard limit for early notice.',
                type: 'number',
                default: 40,
                min: 0,
                max: 1000000,
                step: 0.5,
            },
            {
                path: 'budget.fallback_to_local_on_limit',
                label: 'Fall back to local on limit',
                help: 'When the daily hard limit is hit, route to the local model instead of refusing the request.',
                type: 'boolean',
                default: true,
            },
        ],
    },
    {
        id: 'logging',
        title: 'Logging & Audit',
        description: 'Log verbosity and the audit trail.',
        fields: [
            {
                path: 'logging.level',
                label: 'Log level',
                help: 'Verbosity of application logs. "debug" is noisy; "info" is the production default.',
                type: 'enum',
                default: 'info',
                options: [
                    { value: 'debug', label: 'debug' },
                    { value: 'info', label: 'info' },
                    { value: 'warning', label: 'warning' },
                    { value: 'error', label: 'error' },
                ],
                restartRequired: true,
            },
            {
                path: 'logging.audit_trail.enabled',
                label: 'Audit trail',
                help: 'Record a structured audit log of requests and security decisions.',
                type: 'boolean',
                default: true,
            },
            {
                path: 'logging.audit_trail.mask_pii',
                label: 'Mask PII in audit log',
                help: 'Redact detected personal data before it is written to the audit trail. Leave on unless you have a specific reason.',
                type: 'boolean',
                default: true,
            },
        ],
    },
    {
        id: 'rotation',
        title: 'Rotation & Failover',
        description: 'How requests are spread across endpoints and retried on failure.',
        fields: [
            {
                path: 'rotation.strategy',
                label: 'Rotation strategy',
                help: 'How the proxy picks among healthy endpoints for each request.',
                type: 'enum',
                default: 'round_robin',
                options: [
                    { value: 'round_robin', label: 'round_robin' },
                    { value: 'weighted', label: 'weighted' },
                    { value: 'least_used', label: 'least_used' },
                    { value: 'random', label: 'random' },
                ],
                advanced: true,
            },
            {
                path: 'rotation.failover.enabled',
                label: 'Failover',
                help: 'On an endpoint error, automatically retry the next endpoint in the chain.',
                type: 'boolean',
                default: true,
                advanced: true,
            },
            {
                path: 'rotation.failover.max_retries',
                label: 'Max retries',
                help: 'How many alternate endpoints to try before giving up on a request.',
                type: 'number',
                default: 3,
                min: 0,
                max: 20,
                advanced: true,
            },
        ],
    },
];

/** Flat list of every managed field, for lookups and validation. */
export const ALL_FIELDS: ConfigField[] = CONFIG_SECTIONS.flatMap((s) => s.fields);

/** Coerce a raw form string to the field's typed value (for number/enum/string). */
export function fieldError(field: ConfigField, value: Scalar | null | undefined): string | null {
    if (field.type === 'number') {
        const n = typeof value === 'number' ? value : Number(value);
        if (Number.isNaN(n)) return 'Must be a number';
        if (field.min != null && n < field.min) return `Must be ≥ ${field.min}`;
        if (field.max != null && n > field.max) return `Must be ≤ ${field.max}`;
    }
    if (field.type === 'enum') {
        const opts = (field.options ?? []).map((o) => o.value);
        if (typeof value !== 'string' || !opts.includes(value)) return `Must be one of: ${opts.join(', ')}`;
    }
    return null;
}
