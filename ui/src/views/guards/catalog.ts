import type { GuardSpec } from './types';

/**
 * Curated catalog of every guard the UI surfaces. Order matches the visual
 * grid (most-toggled at top-left → diagnostic-only at bottom-right).
 *
 * Provenance copy answers the "why is this here" question that the
 * provenance tooltip surfaces — naming triggers, the threat it counters,
 * and where to flip it when read-only (config key, env var).
 */
export const GUARDS: GuardSpec[] = [
    {
        key: 'injection_guard',
        name: 'Injection Guard',
        iconSvg:
            '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"/></svg>',
        description:
            'Regex threat scoring with 8 injection patterns. Blocks "ignore previous instructions", role-play attacks, and system-prompt extraction.',
        toggleable: true,
        intent: 'primary',
        provenance:
            'Triggered on prompt body ingress. Pattern set lives in plugins/ring2/injection_guard.py — flip features.injection_guard at runtime via the toggle here, persisted across restarts.',
    },
    {
        key: 'language_guard',
        name: 'Language Guard',
        iconSvg:
            '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 5h12M9 3v2m1.048 9.5A18.022 18.022 0 016.412 9m6.088 9h7M11 21l5-10 5 10M12.751 5C11.783 10.77 8.07 15.61 3 18.129"/></svg>',
        description:
            'Detects anomalous charsets, control characters, zero-width abuse, and steganography in LLM responses.',
        toggleable: true,
        intent: 'warning',
        provenance:
            'Triggered on response body egress (Ring 4). Catches Unicode-class smuggling and homoglyph attacks before tokens reach the client.',
    },
    {
        key: 'link_sanitizer',
        name: 'Link Sanitizer',
        iconSvg:
            '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1"/></svg>',
        description:
            'Strips blocked domains and suspicious URLs from prompts and responses. Prevents phishing and malicious link injection.',
        toggleable: true,
        intent: 'info',
        provenance:
            'Runs on both prompt ingress and response egress. Blocklist sourced from security.link_blocklist in config.yaml; feature flag controls runtime gate.',
    },
    {
        key: 'pii_masker',
        name: 'PII Masker',
        iconSvg:
            '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13.875 18.825A10.05 10.05 0 0112 19c-4.478 0-8.268-2.943-9.543-7a9.97 9.97 0 011.563-3.029m5.858.908a3 3 0 114.243 4.243M9.878 9.878l4.242 4.242M9.88 9.88l-3.29-3.29m7.532 7.532l3.29 3.29M3 3l3.59 3.59m0 0A9.953 9.953 0 0112 5c4.478 0 8.268 2.943 9.543 7a10.025 10.025 0 01-4.132 5.411m0 0L21 21"/></svg>',
        description:
            'Dual-mode PII detection: Presidio NLP (opt-in) + regex fallback. Masks emails, phones, SSNs, credit cards, IBANs.',
        toggleable: false,
        staticStatus: 'ALWAYS ON',
        intent: 'primary',
        provenance:
            'Required by data-protection guarantees — cannot be disabled at runtime. To opt out for a specific tenant, exempt the API key in security.pii_masker.exclude_keys in config.yaml.',
    },
    {
        key: 'firewall',
        name: 'ASGI Firewall',
        iconSvg:
            '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17.657 18.657A8 8 0 016.343 7.343S7 9 9 10c0-2 .5-5 2.986-7C14 5 16.09 5.777 17.656 7.343A7.975 7.975 0 0120 13a7.975 7.975 0 01-2.343 5.657z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.879 16.121A3 3 0 1012.015 11L11 14H9c0 .768.293 1.536.879 2.121z"/></svg>',
        description:
            'Byte-level ASGI middleware. 11 banned injection signatures scanned at L7 before any route handler.',
        toggleable: false,
        intent: 'danger',
        provenance:
            'Runs ahead of every route handler. Toggle via LLM_PROXY_FIREWALL_ENABLED env var or security.firewall.enabled in config.yaml — restart required for either to take effect.',
    },
    {
        key: 'rate_limiter',
        name: 'Rate Limiter',
        iconSvg:
            '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>',
        description:
            'Per-IP and per-API-key token bucket with auto-eviction. Configurable burst capacity and Retry-After headers.',
        toggleable: false,
        staticStatus: 'MIDDLEWARE',
        intent: 'info',
        provenance:
            'Always-on middleware. Bucket caps and burst sizes are read from security.rate_limit.* in config.yaml — restart required to change them.',
    },
    {
        key: 'zero_trust',
        name: 'Zero Trust',
        iconSvg:
            '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z"/></svg>',
        description:
            'mTLS certificate validation + Tailscale identity verification. Per-request identity context injection into upstream headers.',
        toggleable: false,
        staticStatus: 'CONFIG',
        intent: 'info',
        provenance:
            'Engaged when security.zt.enabled=true and a valid identity backend is configured (mTLS or Tailscale). Identity context flows to upstream requests as the X-LLM-Identity-* header set.',
    },
    {
        key: 'circuit_breaker',
        name: 'Circuit Breaker',
        iconSvg:
            '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/></svg>',
        description:
            'Per-endpoint failure tracking. Auto-opens after 5 failures, recovers via half-open probe after 60s cooldown.',
        toggleable: false,
        staticStatus: 'AUTO',
        intent: 'warning',
        provenance:
            'Automatic — opens individual endpoints, never the whole proxy. Reset a stuck circuit from the Endpoints tab → Inspect → Reset breaker.',
    },
];
