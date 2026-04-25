/**
 * Plugins view orchestrator. Imported dynamically from
 * `components/plugins.js` so Vite resolves the .ts source at build while
 * the source-tree fallback degrades gracefully.
 *
 * Layout: header toolbar (Rollback / + Install / Reload) + collapsed
 * install form + grid of plugin cards. Stats are fetched in parallel
 * with the registry list so the cards render even if /stats 503s.
 */
import { createEmptyState, createErrorState, createSkeleton } from '../../ui';
import { createInstallPluginForm, type InstallFormHandle } from './InstallForm';
import { createPluginCard } from './PluginCard';
import type { InstallPluginInput, Plugin, PluginStats, PluginStatsMap } from './types';

export interface PluginsApi {
    fetchPlugins: () => Promise<{ plugins?: Plugin[] } | Plugin[] | null>;
    fetchPluginStats: () => Promise<PluginStatsMap | null>;
    togglePlugin: (name: string, enabled: boolean) => Promise<unknown>;
    installPlugin: (input: InstallPluginInput) => Promise<{ status?: string; detail?: string } | unknown>;
    uninstallPlugin: (name: string) => Promise<unknown>;
    rollbackPlugins: () => Promise<unknown>;
    /** POSTs to /api/v1/plugins/hot-swap. Reload button. */
    reloadPlugins: () => Promise<unknown>;
}

export interface MountPluginsOptions {
    api: PluginsApi;
    toast?: (message: string, kind?: 'success' | 'error' | 'warning' | 'info') => void;
    pollIntervalMs?: number;
    poll?: (fn: () => void, intervalMs: number) => () => void;
}

export interface PluginsHosts {
    grid: HTMLElement | null;
    formHost: HTMLElement | null;
    rollbackBtn: HTMLElement | null;
    installToggle: HTMLElement | null;
    reloadBtn: HTMLElement | null;
}

function normalizePlugins(data: unknown): Plugin[] {
    if (Array.isArray(data)) return data as Plugin[];
    if (data && typeof data === 'object' && Array.isArray((data as { plugins?: unknown }).plugins)) {
        return (data as { plugins: Plugin[] }).plugins;
    }
    return [];
}

export function mountPluginsView(hosts: PluginsHosts, opts: MountPluginsOptions): () => void {
    if (!hosts.grid || !hosts.formHost) return () => {};

    let plugins: Plugin[] = [];
    let stats: PluginStatsMap = {};
    let formHandle: InstallFormHandle;

    formHandle = createInstallPluginForm({
        submit: opts.api.installPlugin,
        onSuccess: () => void refresh(),
        toast: opts.toast,
    });
    hosts.formHost.replaceChildren(formHandle.root);

    function paint(): void {
        if (plugins.length === 0) {
            hosts.grid!.replaceChildren(
                createEmptyState({
                    title: 'No plugins registered',
                    description:
                        'Use + Install above to add a plugin to the pipeline, or check that ./plugins is mounted in your config.',
                    testId: 'plugins-empty',
                })
            );
            return;
        }
        const fragment = document.createDocumentFragment();
        for (const p of plugins) {
            fragment.appendChild(
                createPluginCard(p, stats[p.name] as PluginStats | undefined, {
                    onToggle: async (name, next) => {
                        await opts.api.togglePlugin(name, next);
                    },
                    onUninstall: async (name) => {
                        await opts.api.uninstallPlugin(name);
                    },
                    refresh,
                    toast: opts.toast,
                })
            );
        }
        hosts.grid!.replaceChildren(fragment);
    }

    function showLoading(): void {
        const fragment = document.createDocumentFragment();
        for (let i = 0; i < 4; i++)
            fragment.appendChild(createSkeleton({ shape: 'block', height: '8rem', ariaLabel: '' }));
        hosts.grid!.replaceChildren(fragment);
    }

    function showError(detail?: string): void {
        hosts.grid!.replaceChildren(
            createErrorState({
                title: 'Backend offline',
                description: 'Start the gateway to load the plugin pipeline.',
                detail,
                onRetry: () => void refresh(),
                testId: 'plugins-error',
            })
        );
    }

    async function refresh(): Promise<void> {
        try {
            const [pluginsRaw, statsRaw] = await Promise.all([
                opts.api.fetchPlugins(),
                opts.api.fetchPluginStats().catch(() => ({})),
            ]);
            plugins = normalizePlugins(pluginsRaw);
            stats = (statsRaw ?? {}) as PluginStatsMap;
            paint();
        } catch (err) {
            showError((err as Error)?.message);
        }
    }

    if (hosts.installToggle) {
        const fresh = hosts.installToggle.cloneNode(true) as HTMLElement;
        hosts.installToggle.parentNode?.replaceChild(fresh, hosts.installToggle);
        fresh.addEventListener('click', () => {
            if (formHandle.isOpen()) formHandle.close();
            else formHandle.open();
        });
    }

    if (hosts.rollbackBtn) {
        const fresh = hosts.rollbackBtn.cloneNode(true) as HTMLElement;
        hosts.rollbackBtn.parentNode?.replaceChild(fresh, hosts.rollbackBtn);
        fresh.addEventListener('click', async () => {
            const { confirm } = await import('../../ui');
            const ok = await confirm({
                title: 'Rollback plugin configuration',
                message: 'Revert to the previous plugin configuration snapshot? Any changes made since will be lost.',
                confirmLabel: 'Rollback',
                danger: true,
            });
            if (!ok) return;
            try {
                await opts.api.rollbackPlugins();
                opts.toast?.('Plugin configuration rolled back', 'success');
                await refresh();
            } catch (err) {
                opts.toast?.(`Rollback failed: ${(err as Error)?.message ?? err}`, 'error');
            }
        });
    }

    if (hosts.reloadBtn) {
        const fresh = hosts.reloadBtn.cloneNode(true) as HTMLElement;
        hosts.reloadBtn.parentNode?.replaceChild(fresh, hosts.reloadBtn);
        fresh.addEventListener('click', async () => {
            try {
                await opts.api.reloadPlugins();
                opts.toast?.('Plugins hot-swapped', 'success');
                await refresh();
            } catch (err) {
                opts.toast?.(`Plugin reload failed: ${(err as Error)?.message ?? err}`, 'error');
            }
        });
    }

    showLoading();
    void refresh();

    const interval = opts.pollIntervalMs ?? 30_000;
    const stopPoll = opts.poll
        ? opts.poll(refresh, interval)
        : (() => {
              const id = setInterval(refresh, interval);
              return () => clearInterval(id);
          })();

    return stopPoll;
}

export { createInstallPluginForm } from './InstallForm';
export { createPluginCard } from './PluginCard';
export type { Plugin, PluginStats, PluginStatsMap, InstallPluginInput, RingHook } from './types';
