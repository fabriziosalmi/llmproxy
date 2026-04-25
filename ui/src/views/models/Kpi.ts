import { createMetricTile, type MetricTileOptions } from '../../ui';

export interface ModelsKpiData {
    /** Total models across all providers (chat + embedding). */
    total: number;
    /** Distinct owned_by values. */
    providers: number;
    /** Subset matching one of EMBEDDING_PREFIXES. */
    embedding: number;
}

interface KpiSpec {
    key: keyof ModelsKpiData;
    label: string;
    intent: NonNullable<MetricTileOptions['intent']>;
    provenance: string;
    format: (n: number) => string;
}

const KPIS: KpiSpec[] = [
    {
        key: 'total',
        label: 'Active Models',
        intent: 'info',
        provenance: 'Count of every model returned by GET /v1/models — chat + embedding combined.',
        format: (n) => n.toLocaleString(),
    },
    {
        key: 'providers',
        label: 'Providers',
        intent: 'success',
        provenance:
            'Distinct owned_by values across the model set. Reflects how many configured backends are answering.',
        format: (n) => n.toLocaleString(),
    },
    {
        key: 'embedding',
        label: 'Embedding Models',
        intent: 'primary',
        provenance:
            "Models whose id starts with one of the embedding prefixes (text-embedding-, bge-, nomic-embed-, …). Updated alongside the backend's content_router.",
        format: (n) => n.toLocaleString(),
    },
];

export function renderModelsKpis(container: HTMLElement, data: ModelsKpiData | null, error?: string): void {
    container.replaceChildren();
    container.className = 'grid grid-cols-1 md:grid-cols-3 gap-4 mb-6';
    container.setAttribute('data-testid', 'models-kpi-grid');
    for (const spec of KPIS) {
        container.appendChild(
            createMetricTile({
                label: spec.label,
                value: data ? spec.format(data[spec.key]) : '',
                intent: spec.intent,
                provenance: spec.provenance,
                loading: data === null && !error,
                error,
                testId: `kpi-${spec.key === 'total' ? 'active-models' : spec.key === 'embedding' ? 'embedding-models' : 'providers'}`,
            })
        );
    }
}
