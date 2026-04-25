/** Subset of GET /api/v1/version. */
export interface VersionInfo {
    version?: string;
}

/** Subset of GET /api/v1/service-info. */
export interface ServiceInfo {
    url?: string;
}

/** GET /api/v1/identity/config. */
export interface IdentityConfig {
    enabled?: boolean;
}

/** GET /api/v1/identity/me. */
export interface IdentityMe {
    authenticated?: boolean;
    provider?: string;
    email?: string;
    roles?: string[];
    permissions?: string[];
}

/** GET /api/v1/rbac/roles → { role_name: ['perm:read', ...] } */
export type RbacRoles = Record<string, string[]>;

export type WebhookTarget = 'slack' | 'teams' | 'discord' | 'generic' | string;

export interface WebhookEndpoint {
    name: string;
    target: WebhookTarget;
    events: string[];
}

export interface WebhooksConfig {
    enabled?: boolean;
    endpoints?: WebhookEndpoint[];
    event_types?: string[];
}

export interface ExportFile {
    name: string;
    size_bytes: number;
}

export interface ExportStatus {
    enabled?: boolean;
    output_dir?: string;
    scrub_pii?: boolean;
    compress?: boolean;
    files?: ExportFile[];
}
