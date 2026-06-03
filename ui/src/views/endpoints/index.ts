/**
 * Endpoints view orchestrator. Imported dynamically from
 * `components/registry.js` so Vite resolves the .ts source at build time
 * while the source-tree fallback degrades gracefully.
 *
 * Strangler fig: the legacy table renderer in registry.js still runs
 * during the dynamic-import roundtrip so the page is functional from
 * the first paint. Once mounted, the TS view replaces the markup.
 */
import { createErrorState } from '../../ui';
import { createAddEndpointForm, type AddFormHandle } from './AddForm';
import { createRegistryEmptyState } from './EmptyState';
import { createRegistryTable } from './RegistryTable';
import type { AddEndpointInput, Endpoint } from './types';

export interface EndpointsApi {
    fetchRegistry: () => Promise<Endpoint[]>;
    addEndpoint: (input: AddEndpointInput) => Promise<unknown>;
    probeEndpoint: (
        id: string
    ) => Promise<{ ok?: boolean; status?: number; latency_ms?: number; models_count?: number }>;
    toggleEndpoint: (id: string) => Promise<unknown>;
    deleteEndpoint: (id: string) => Promise<unknown>;
    updatePriority: (id: string, priority: number) => Promise<unknown>;
    resetCircuitBreaker: (id: string) => Promise<unknown>;
}

export interface MountEndpointsOptions {
    api: EndpointsApi;
    toast?: (message: string, kind?: 'success' | 'error' | 'warning' | 'info') => void;
    /** Polling interval for the registry refresh. Defaults to 10s. */
    pollIntervalMs?: number;
    /** Polling driver (defaults to setInterval). */
    poll?: (fn: () => void, intervalMs: number) => () => void;
    /** Initial seed list — usually from the global store. */
    initial?: Endpoint[];
}

export interface EndpointsHosts {
    /** Container that holds the toggle button + add form + registry. */
    view: HTMLElement | null;
    /** Existing toggle button — wired to open the add form. */
    addToggle: HTMLElement | null;
    /** Container where the registry table or empty state is rendered. */
    registry: HTMLElement | null;
    /** Container where the legacy form lives — replaced by the new form. */
    formHost: HTMLElement | null;
}

export function mountEndpointsView(hosts: EndpointsHosts, opts: MountEndpointsOptions): () => void {
    if (!hosts.view || !hosts.registry || !hosts.formHost) {
        return () => {};
    }

    let endpoints: Endpoint[] = opts.initial ?? [];
    let formHandle: AddFormHandle | null = null;

    const refresh = async (): Promise<void> => {
        try {
            const next = await opts.api.fetchRegistry();
            endpoints = Array.isArray(next) ? next : [];
            paint();
        } catch (err) {
            hosts.registry?.replaceChildren(
                createErrorState({
                    title: 'Failed to load registry',
                    description: 'The /api/v1/registry endpoint did not respond.',
                    detail: (err as Error)?.message,
                    onRetry: () => void refresh(),
                    testId: 'registry-error',
                })
            );
        }
    };

    formHandle = createAddEndpointForm({
        submit: opts.api.addEndpoint,
        onSuccess: () => void refresh(),
        toast: opts.toast,
    });
    hosts.formHost.replaceChildren(formHandle.root);

    if (hosts.addToggle) {
        const fresh = hosts.addToggle.cloneNode(true) as HTMLElement;
        hosts.addToggle.parentNode?.replaceChild(fresh, hosts.addToggle);
        fresh.addEventListener('click', () => {
            if (!formHandle) return;
            if (formHandle.isOpen()) formHandle.close();
            else formHandle.open();
        });
    }

    function paint(): void {
        if (!hosts.registry) return;
        if (endpoints.length === 0) {
            hosts.registry.replaceChildren(
                createRegistryEmptyState({
                    onAdd: () => {
                        formHandle?.open();
                        formHandle?.root.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    },
                })
            );
            return;
        }

        const table = createRegistryTable(endpoints, {
            onResetCircuitBreaker: async (id) => {
                await opts.api.resetCircuitBreaker(id);
            },
            onProbeEndpoint: async (id) => opts.api.probeEndpoint(id),
            onToggleEndpoint: async (id) => {
                await opts.api.toggleEndpoint(id);
            },
            onDeleteEndpoint: async (id) => {
                await opts.api.deleteEndpoint(id);
            },
            onUpdatePriority: async (id, next) => {
                await opts.api.updatePriority(id, next);
            },
            refresh,
            toast: opts.toast,
        });
        hosts.registry.replaceChildren(table.root);
    }

    paint();
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

export { createAddEndpointForm } from './AddForm';
export { createRegistryTable } from './RegistryTable';
export { createRegistryEmptyState } from './EmptyState';
export type { Endpoint, AddEndpointInput } from './types';
