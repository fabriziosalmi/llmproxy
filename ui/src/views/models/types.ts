/** Single model entry from GET /v1/models. */
export interface Model {
    id: string;
    owned_by: string;
    object?: string;
    created?: number;
}

/** Kept in sync with the legacy detector — when a new embedding family ships,
 *  add the prefix here AND in the backend's content_router. */
export const EMBEDDING_PREFIXES = [
    'text-embedding',
    'embedding-',
    'nomic-embed',
    'mxbai-embed',
    'all-minilm',
    'bge-',
    'snowflake-arctic',
    'mistral-embed',
];

export function isEmbeddingModel(id: string): boolean {
    const lower = id.toLowerCase();
    return EMBEDDING_PREFIXES.some((p) => lower.startsWith(p));
}

/**
 * Provider → Tailwind text-color class. The Badge primitive's six intents
 * compress some of these (rose covers both "primary" use cases), so we keep
 * a bespoke map here for the finer-grained provider palette.
 */
export const PROVIDER_COLOR: Record<string, string> = {
    openai: 'text-emerald-400',
    anthropic: 'text-amber-400',
    google: 'text-sky-400',
    azure: 'text-blue-400',
    ollama: 'text-slate-400',
    groq: 'text-orange-400',
    together: 'text-purple-400',
    mistral: 'text-indigo-400',
    deepseek: 'text-cyan-400',
    xai: 'text-rose-400',
    perplexity: 'text-teal-400',
};

export function providerColor(name: string): string {
    return PROVIDER_COLOR[name.toLowerCase()] ?? 'text-slate-400';
}
