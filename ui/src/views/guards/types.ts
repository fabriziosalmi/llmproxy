import type { BadgeIntent } from '../../ui';

/** A single guard's catalog entry — drives both the visual and the toggle wiring. */
export interface GuardSpec {
    /** Stable key matching the backend feature flag (`features[key]`). */
    key: string;
    /** Display name. */
    name: string;
    /** Inline SVG markup. Caller is responsible for sanitizing. */
    iconSvg: string;
    /** One-paragraph description shown on the card body. */
    description: string;
    /** Whether the user can flip this from the UI. False = read-only status pill. */
    toggleable: boolean;
    /** Color tied to status copy + badge. */
    intent: BadgeIntent;
    /**
     * "Why this guard exists" — surfaces in the provenance tooltip. Should
     * answer: what triggers it, what it blocks, where it can be disabled if
     * read-only (e.g. config path).
     */
    provenance: string;
    /** Static status pill text when toggleable=false (e.g. "ALWAYS ON", "CONFIG", "AUTO"). */
    staticStatus?: string;
}

/** Live state slice the Guards view depends on. Mirrors the global store. */
export interface GuardsState {
    /** Per-guard enabled flags (`features[key] !== false`). */
    features: Record<string, boolean | undefined>;
    /** Master proxy enable flag. */
    proxyEnabled: boolean;
    /** Priority-steering enable flag. */
    priorityMode: boolean;
    /** Live firewall status (driven by env/config, not user). */
    firewall: { enabled: boolean; disabled_reason: string | null };
}
