/**
 * Guards view orchestrator. Imported dynamically from `components/guards.js`
 * (the legacy shell) so the Vite build resolves the .ts source and the
 * source-tree fallback degrades gracefully — same pattern as the Threats view.
 *
 * Strangler fig: the Cache Performance card and the Operations buttons
 * (reset firewall, clear caches, reset security, reload config) still live
 * in the legacy component. They migrate when each becomes painful.
 */
import { mountGuardsGrid } from './Grid';
import { mountToggleCard } from './Toggles';
import type { GuardsState } from './types';

export interface GuardsApi {
    fetchGuardsStatus: () => Promise<{
        firewall?: { enabled?: boolean; disabled_reason?: string | null };
        features?: Record<string, boolean | undefined>;
        proxy_enabled?: boolean;
        priority_mode?: boolean;
    } | null>;
    toggleProxy: (next: boolean) => Promise<{ enabled: boolean }>;
    togglePriorityMode: (next: boolean) => Promise<{ enabled: boolean }>;
    toggleFeature: (name: string, next: boolean) => Promise<{ enabled: boolean }>;
}

export interface MountGuardsOptions {
    api: GuardsApi;
    /** Toast helper. Optional. */
    toast?: (message: string, kind?: 'success' | 'error' | 'warning' | 'info') => void;
    /** Initial seeded state from the global store. */
    initial?: Partial<GuardsState>;
    /** Polling interval for the status refresh. Defaults to 10s. */
    pollIntervalMs?: number;
    /** Polling driver (defaults to setInterval). */
    poll?: (fn: () => void, intervalMs: number) => () => void;
}

export function mountGuardsView(
    hosts: { master: HTMLElement | null; priority: HTMLElement | null; grid: HTMLElement | null },
    opts: MountGuardsOptions
): () => void {
    let state: GuardsState = {
        features: opts.initial?.features ?? {},
        proxyEnabled: opts.initial?.proxyEnabled ?? true,
        priorityMode: opts.initial?.priorityMode ?? false,
        firewall: opts.initial?.firewall ?? { enabled: true, disabled_reason: null },
    };

    const masterToggle = hosts.master
        ? mountToggleCard(hosts.master, {
              title: 'Gateway Status',
              description: 'Master proxy enable/disable',
              initialChecked: state.proxyEnabled,
              onToggle: opts.api.toggleProxy,
              toast: opts.toast,
              successLabel: (e) => `Proxy ${e ? 'enabled' : 'disabled'}`,
              failureLabel: (m) => `Proxy toggle failed: ${m}`,
              testId: 'guards-master-toggle',
          })
        : null;

    const priorityToggle = hosts.priority
        ? mountToggleCard(hosts.priority, {
              title: 'Priority Steering',
              description: 'Route to highest-priority endpoint only',
              initialChecked: state.priorityMode,
              onToggle: opts.api.togglePriorityMode,
              toast: opts.toast,
              successLabel: (e) => `Priority steering ${e ? 'enabled' : 'disabled'}`,
              failureLabel: (m) => `Priority steering toggle failed: ${m}`,
              testId: 'guards-priority-toggle',
          })
        : null;

    const grid = hosts.grid
        ? mountGuardsGrid(hosts.grid, state, {
              toggleGuard: opts.api.toggleFeature,
              toast: opts.toast,
          })
        : null;

    const refresh = async (): Promise<void> => {
        try {
            const data = await opts.api.fetchGuardsStatus();
            if (!data) return;
            state = {
                features: data.features ?? state.features,
                proxyEnabled: data.proxy_enabled ?? state.proxyEnabled,
                priorityMode: data.priority_mode ?? state.priorityMode,
                firewall: {
                    enabled: data.firewall?.enabled !== false,
                    disabled_reason: data.firewall?.disabled_reason ?? null,
                },
            };
            grid?.setState(state);
            masterToggle?.setChecked(state.proxyEnabled);
            priorityToggle?.setChecked(state.priorityMode);
        } catch (err) {
            grid?.setError('Backend unreachable.', (err as Error)?.message);
        }
    };

    void refresh();

    const interval = opts.pollIntervalMs ?? 10_000;
    const stopPoll = opts.poll
        ? opts.poll(refresh, interval)
        : (() => {
              const id = setInterval(refresh, interval);
              return () => clearInterval(id);
          })();

    return stopPoll;
}

export { mountGuardsGrid } from './Grid';
export { mountToggleCard } from './Toggles';
export { GUARDS } from './catalog';
export type { GuardSpec, GuardsState } from './types';
