export type CircuitState = 'closed' | 'open' | 'half_open';

/** Single registry row as returned by GET /api/v1/registry. */
export interface Endpoint {
    id: string;
    name?: string;
    url: string;
    provider?: string;
    /** "Live" / "IGNORED" / "DEGRADED" / etc. — surfaced as a status badge. */
    status?: string;
    circuit_state?: CircuitState | string;
    failure_count?: number;
    failure_threshold?: number;
    latency?: string | number;
    priority?: number;
    enabled?: boolean;
    healthy?: boolean;
    models?: string[];
}

/** POST /api/v1/registry payload. */
export interface AddEndpointInput {
    id: string;
    url: string;
    provider: string;
    priority: number;
    api_key?: string;
    models: string[];
}

export const PROVIDER_OPTIONS: Array<{ value: string; label: string }> = [
    { value: 'openai-compatible', label: 'OpenAI-compatible (local / vLLM / LM Studio)' },
    { value: 'openai', label: 'OpenAI' },
    { value: 'anthropic', label: 'Anthropic' },
    { value: 'google', label: 'Google' },
    { value: 'azure', label: 'Azure' },
    { value: 'ollama', label: 'Ollama' },
    { value: 'groq', label: 'Groq' },
    { value: 'together', label: 'Together' },
    { value: 'mistral', label: 'Mistral' },
    { value: 'deepseek', label: 'DeepSeek' },
    { value: 'openrouter', label: 'OpenRouter' },
];
