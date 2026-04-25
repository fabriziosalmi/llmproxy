/**
 * Models view orchestrator. Imported dynamically from `components/models.js`
 * so the source-tree fallback degrades gracefully when no Vite build is
 * present.
 *
 * Layout: 3 KPI tiles (Active / Providers / Embedding) + a debounced
 * search box + two tables (chat first, embedding second when any). Empty
 * states surface when /v1/models returns nothing or when the search query
 * filters everything out.
 */
import { createEmptyState, createErrorState } from '../../ui';
import { renderModelsKpis, type ModelsKpiData } from './Kpi';
import { createModelsTable } from './ModelsTable';
import { isEmbeddingModel, type Model } from './types';

export interface ModelsApi {
    fetchModels: () => Promise<{ data?: Model[] } | Model[] | null>;
}

export interface MountModelsOptions {
    api: ModelsApi;
    pollIntervalMs?: number;
    poll?: (fn: () => void, intervalMs: number) => () => void;
    initial?: Model[];
}

export interface ModelsHosts {
    kpis: HTMLElement | null;
    search: HTMLElement | null;
    table: HTMLElement | null;
}

const SEARCH_DEBOUNCE_MS = 150;

function deriveKpis(models: Model[]): ModelsKpiData {
    const providers = new Set<string>();
    let embedding = 0;
    for (const m of models) {
        providers.add(m.owned_by);
        if (isEmbeddingModel(m.id)) embedding++;
    }
    return { total: models.length, providers: providers.size, embedding };
}

function filterModels(models: Model[], query: string): Model[] {
    if (!query) return models;
    const q = query.toLowerCase();
    return models.filter((m) => m.id.toLowerCase().includes(q) || m.owned_by.toLowerCase().includes(q));
}

export function mountModelsView(hosts: ModelsHosts, opts: MountModelsOptions): () => void {
    if (!hosts.kpis || !hosts.table) return () => {};

    let allModels: Model[] = opts.initial ?? [];
    let query = '';

    function paint(): void {
        const kpis = deriveKpis(allModels);
        renderModelsKpis(hosts.kpis!, kpis);

        const visible = filterModels(allModels, query);
        const chat = visible.filter((m) => !isEmbeddingModel(m.id));
        const embedding = visible.filter((m) => isEmbeddingModel(m.id));

        const target = hosts.table!;
        target.replaceChildren();

        if (allModels.length === 0) {
            target.appendChild(
                createEmptyState({
                    title: 'No models registered',
                    description:
                        'GET /v1/models returned nothing. Add an endpoint with a model list, or check that LLM_PROXY_ENDPOINT_*_MODELS is populated in .env.',
                    testId: 'models-empty',
                })
            );
            return;
        }

        if (visible.length === 0) {
            target.appendChild(
                createEmptyState({
                    title: 'No models match this filter',
                    description: `Nothing matched "${query}". Clear the search or broaden the query.`,
                    testId: 'models-no-match',
                })
            );
            return;
        }

        target.appendChild(createModelsTable(chat).root);

        if (embedding.length > 0) {
            const heading = document.createElement('p');
            heading.className = 'text-[9px] font-bold text-rose-400 uppercase tracking-widest mt-6 mb-2';
            heading.textContent = 'Embedding Models';
            target.appendChild(heading);
            target.appendChild(createModelsTable(embedding).root);
        }

        const footer = document.createElement('p');
        footer.className = 'text-[9px] text-slate-600 mt-3 px-1';
        footer.textContent = `${visible.length} models across ${new Set(visible.map((m) => m.owned_by)).size} providers`;
        target.appendChild(footer);
    }

    function showError(message: string, detail?: string): void {
        renderModelsKpis(hosts.kpis!, null, message);
        hosts.table!.replaceChildren(
            createErrorState({
                title: 'Failed to load models',
                description: message,
                detail,
                onRetry: () => void refresh(),
                testId: 'models-error',
            })
        );
    }

    async function refresh(): Promise<void> {
        try {
            const result = await opts.api.fetchModels();
            const list = Array.isArray(result) ? result : (result?.data ?? []);
            allModels = list;
            paint();
        } catch (err) {
            showError('GET /v1/models did not respond.', (err as Error)?.message);
        }
    }

    // Wire the existing search input. cloneNode strips legacy listeners.
    if (hosts.search) {
        const fresh = hosts.search.cloneNode(true) as HTMLInputElement;
        hosts.search.parentNode?.replaceChild(fresh, hosts.search);
        let timer: ReturnType<typeof setTimeout> | null = null;
        fresh.addEventListener('input', () => {
            if (timer) clearTimeout(timer);
            timer = setTimeout(() => {
                query = fresh.value.trim();
                paint();
            }, SEARCH_DEBOUNCE_MS);
        });
    }

    // Show skeletons immediately, then refresh.
    renderModelsKpis(hosts.kpis, null);
    paint();
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

export { renderModelsKpis } from './Kpi';
export { createModelsTable } from './ModelsTable';
export type { Model } from './types';
