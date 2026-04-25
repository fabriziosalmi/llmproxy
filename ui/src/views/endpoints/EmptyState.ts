/**
 * Onboarding empty state shown when the registry has zero endpoints.
 * Wraps the EmptyState primitive with an env-var hint for users who prefer
 * not to use the form (LM Studio / vLLM / Ollama path).
 */
import { createButton, createEmptyState } from '../../ui';

export interface RegistryEmptyOptions {
    /** Called when the user clicks "Add first endpoint". */
    onAdd: () => void;
}

const ENV_HINT = `# In .env then restart
LLM_PROXY_ENDPOINT_LOCAL_URL=http://192.168.1.50:1234/v1
LLM_PROXY_ENDPOINT_LOCAL_MODELS=llama-3.3-70b`;

export function createRegistryEmptyState(opts: RegistryEmptyOptions): HTMLElement {
    const root = createEmptyState({
        title: 'Welcome to LLMProxy',
        description:
            'No endpoints yet. Add your first provider to start routing requests. The proxy is running in onboarding mode — inference calls will 503 until an endpoint is added.',
        action: {
            label: 'Add first endpoint',
            onClick: opts.onAdd,
            testId: 'onboarding-add-ep',
        },
    });
    root.setAttribute('data-testid', 'registry-empty');

    // Append the env-var hint as a collapsed details block. Not part of the
    // EmptyState primitive — it's bespoke onboarding copy for this view.
    const details = document.createElement('details');
    details.className = 'text-left max-w-lg mx-auto mt-4';
    const summary = document.createElement('summary');
    summary.className = 'text-[10px] text-slate-500 cursor-pointer hover:text-slate-300';
    summary.textContent = 'Prefer env vars? (LM Studio, vLLM, Ollama)';
    details.appendChild(summary);
    const pre = document.createElement('pre');
    pre.className = 'text-[10px] text-slate-400 mt-2 bg-black/30 rounded p-3 overflow-x-auto';
    const code = document.createElement('code');
    code.textContent = ENV_HINT;
    pre.appendChild(code);
    details.appendChild(pre);
    root.appendChild(details);

    return root;
}

// Re-export createButton just to keep the barrel happy when consumers want
// to render the same affordance outside this view.
export { createButton };
