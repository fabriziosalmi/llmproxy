/**
 * Settings view orchestrator. Imported dynamically from
 * `components/settings.js` so Vite resolves the .ts source at build
 * while the source-tree fallback degrades gracefully.
 *
 * Each section is mounted independently and refreshes on its own — a 503
 * on /api/v1/rbac/roles doesn't blank out /api/v1/webhooks. Sections show
 * skeletons while loading, ErrorState with retry on failure, and
 * EmptyState when the backend reports the feature is disabled.
 */
import { mountDataExport, type ExportApi } from './DataExport';
import { mountIdentity, type IdentityApi } from './Identity';
import { mountRbacMatrix, type RbacApi } from './RbacMatrix';
import { mountSystemInfo, type SystemInfoApi } from './SystemInfo';
import { mountWebhooks, type WebhooksApi } from './Webhooks';

export interface SettingsApi extends SystemInfoApi, IdentityApi, RbacApi, WebhooksApi, ExportApi {}

export interface MountSettingsOptions {
    api: SettingsApi;
    toast?: (message: string, kind?: 'success' | 'error' | 'warning' | 'info') => void;
}

export interface SettingsHosts {
    identity: HTMLElement | null;
    rbac: HTMLElement | null;
    webhooks: HTMLElement | null;
    export: HTMLElement | null;
    system: HTMLElement | null;
}

export function mountSettingsView(hosts: SettingsHosts, opts: MountSettingsOptions): () => Promise<void> {
    const refreshes: Array<() => Promise<void>> = [];

    if (hosts.identity) refreshes.push(mountIdentity(hosts.identity, opts.api));
    if (hosts.rbac) refreshes.push(mountRbacMatrix(hosts.rbac, opts.api));
    if (hosts.webhooks) {
        const handle = mountWebhooks(hosts.webhooks, opts.api, opts.toast);
        refreshes.push(handle.refresh);
    }
    if (hosts.export) refreshes.push(mountDataExport(hosts.export, opts.api));
    if (hosts.system) refreshes.push(mountSystemInfo(hosts.system, opts.api));

    return async function refreshAll(): Promise<void> {
        await Promise.allSettled(refreshes.map((fn) => fn()));
    };
}

export { mountIdentity } from './Identity';
export { mountRbacMatrix } from './RbacMatrix';
export { mountWebhooks } from './Webhooks';
export { mountDataExport } from './DataExport';
export { mountSystemInfo } from './SystemInfo';
