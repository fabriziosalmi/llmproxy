/**
 * Settings → Active Config (YAML, read-only).
 *
 * Renders what the proxy is ACTUALLY running with — including auto-
 * discovered endpoints, env-merged values, and runtime mutations
 * (preset, cost_weight, …) that on-disk config.yaml hasn't picked up
 * yet. Secrets are scrubbed by the backend before serialisation;
 * the frontend just renders.
 *
 * Honest scope (O.5): no syntax highlighter — Terraform-vibe is the
 * monospace + section structure of the YAML itself, not a rainbow.
 * The Snippet primitive's copy button works out of the box.
 */

import { createSnippet, createErrorState, createSkeleton } from '../../ui';

export interface ConfigYamlApi {
    fetchConfigYaml: () => Promise<{ yaml?: string }>;
}

export function mountConfigYaml(host: HTMLElement, api: ConfigYamlApi): () => Promise<void> {
    // Card scaffold — heading + body that we hot-swap between
    // skeleton / snippet / error.
    const card = document.createElement('div');
    card.className = 'bg-white/[0.03] backdrop-blur-xl rounded-2xl border border-white/[0.06] p-6';
    card.setAttribute('data-testid', 'settings-config-yaml');

    const head = document.createElement('div');
    head.className = 'flex items-center justify-between mb-3';
    const title = document.createElement('h2');
    title.className = 'text-xs font-bold text-white';
    title.textContent = 'Active Config (YAML)';
    head.appendChild(title);
    const note = document.createElement('span');
    note.className = 'text-[10px] text-slate-500 font-mono';
    note.textContent = 'read-only · secrets redacted';
    head.appendChild(note);
    card.appendChild(head);

    const body = document.createElement('div');
    body.appendChild(createSkeleton({ shape: 'block', height: '8rem', ariaLabel: '' }));
    card.appendChild(body);

    host.replaceChildren(card);

    async function refresh(): Promise<void> {
        try {
            const data = await api.fetchConfigYaml();
            const yaml = (data.yaml ?? '').trim();
            if (!yaml) {
                body.replaceChildren(
                    createErrorState({
                        title: 'Empty config',
                        description: 'Backend returned no YAML body.',
                        testId: 'config-yaml-empty',
                    }),
                );
                return;
            }
            const snip = createSnippet({
                language: 'YAML',
                code: yaml,
                testId: 'config-yaml-snippet',
            });
            body.replaceChildren(snip.root);
        } catch (err) {
            body.replaceChildren(
                createErrorState({
                    title: 'Could not load active config',
                    description: 'GET /api/v1/config/yaml failed.',
                    detail: (err as Error)?.message,
                    onRetry: () => void refresh(),
                    testId: 'config-yaml-error',
                }),
            );
        }
    }

    void refresh();
    return refresh;
}
