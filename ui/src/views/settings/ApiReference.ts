/**
 * Settings → API Reference (Q.2)
 *
 * Renders the auth-gated OpenAPI schema as a Snippet (JSON, copy
 * button, monospace). Operators can paste it into Swagger UI / Postman
 * / openapi-generator without restarting the proxy in dev mode.
 *
 * Honest scope: this is a "view & copy" surface. A live Swagger UI
 * embed is a follow-on (would require either a CDN dep or shipping
 * Swagger's static assets).
 */

import { createBadge, createButton, createErrorState, createSkeleton, createSnippet } from '../../ui';

export interface OpenApiSchema {
    openapi?: string;
    info?: { title?: string; version?: string };
    paths?: Record<string, unknown>;
}

export interface ApiReferenceApi {
    fetchOpenApi: () => Promise<OpenApiSchema>;
}

function _summary(schema: OpenApiSchema): { version: string; title: string; pathCount: number } {
    return {
        version: schema.openapi ?? '?',
        title: schema.info?.title ?? 'LLMProxy',
        pathCount: schema.paths ? Object.keys(schema.paths).length : 0,
    };
}

export interface ApiReferenceHandle {
    refresh: () => Promise<void>;
}

export function mountApiReference(host: HTMLElement, api: ApiReferenceApi): ApiReferenceHandle {
    const card = document.createElement('div');
    card.className = 'bg-white/[0.03] backdrop-blur-xl rounded-2xl border border-white/[0.06] p-6';
    card.setAttribute('data-testid', 'settings-api-reference');

    const head = document.createElement('div');
    head.className = 'flex items-center justify-between mb-3';
    const title = document.createElement('h2');
    title.className = 'text-xs font-bold text-white';
    title.textContent = 'API Reference (OpenAPI)';
    head.appendChild(title);
    const summarySlot = document.createElement('div');
    summarySlot.setAttribute('data-testid', 'api-reference-summary');
    head.appendChild(summarySlot);
    card.appendChild(head);

    const body = document.createElement('div');
    body.appendChild(createSkeleton({ shape: 'block', height: '8rem', ariaLabel: '' }));
    card.appendChild(body);

    host.replaceChildren(card);

    function paint(schema: OpenApiSchema): void {
        const sum = _summary(schema);

        // Summary chips in the header — title + version + path count.
        summarySlot.replaceChildren();
        const chips = document.createElement('div');
        chips.className = 'flex items-center gap-2';
        chips.appendChild(
            createBadge({
                label: `OpenAPI ${sum.version}`,
                intent: 'info',
                size: 'sm',
                testId: 'api-reference-version',
            })
        );
        chips.appendChild(
            createBadge({
                label: `${sum.pathCount} paths`,
                intent: 'neutral',
                size: 'sm',
                testId: 'api-reference-path-count',
            })
        );
        summarySlot.appendChild(chips);

        const wrap = document.createElement('div');
        wrap.className = 'space-y-3';

        const intro = document.createElement('p');
        intro.className = 'text-[11px] text-slate-400 leading-relaxed';
        intro.innerHTML =
            'Paste the JSON below into <a href="https://editor.swagger.io" target="_blank" rel="noreferrer" ' +
            'class="text-cyan-400 hover:text-cyan-300 underline">editor.swagger.io</a>, ' +
            '<a href="https://www.postman.com" target="_blank" rel="noreferrer" ' +
            'class="text-cyan-400 hover:text-cyan-300 underline">Postman</a>, or ' +
            '<code class="text-cyan-400">openapi-generator-cli</code> to scaffold a typed client.';
        wrap.appendChild(intro);

        // Pretty-print the spec. 2-space indent stays under typical
        // editor wrap; the Snippet's <pre> handles overflow-x-auto.
        const json = JSON.stringify(schema, null, 2);
        const snip = createSnippet({
            language: 'JSON',
            code: json,
            testId: 'api-reference-snippet',
            caption: `${sum.title} ${sum.pathCount} paths · use Copy ↗ to feed into Swagger / Postman / openapi-generator`,
        });
        wrap.appendChild(snip.root);

        // Affordance: a "View raw" button that opens /api/v1/openapi.json
        // with the bearer key as a query param — most UIs accept that.
        // (Browser-direct fetch needs auth; opening as a tab strips the
        // localStorage token, so we skip that affordance and lean on the
        // Snippet's copy button as the primary path.)

        const ext = document.createElement('div');
        ext.className = 'flex items-center gap-2';
        const swaggerBtn = createButton({
            label: 'Open in Swagger Editor',
            size: 'sm',
            variant: 'ghost',
            testId: 'api-reference-swagger-link',
            onClick: () => {
                window.open('https://editor.swagger.io', '_blank', 'noopener');
            },
        });
        ext.appendChild(swaggerBtn);
        wrap.appendChild(ext);

        body.replaceChildren(wrap);
    }

    async function refresh(): Promise<void> {
        try {
            const schema = await api.fetchOpenApi();
            paint(schema);
        } catch (err) {
            const message = (err as Error)?.message ?? '';
            const looksDisabled = /404/.test(message);
            body.replaceChildren(
                createErrorState({
                    title: looksDisabled ? 'OpenAPI schema not exposed' : 'Could not load OpenAPI schema',
                    description: looksDisabled
                        ? 'The proxy is built without `/api/v1/openapi.json` (likely an old version — the route was added in 1.21.27).'
                        : 'GET /api/v1/openapi.json failed.',
                    detail: message,
                    onRetry: () => void refresh(),
                    testId: 'api-reference-error',
                })
            );
            summarySlot.replaceChildren();
        }
    }

    void refresh();
    return { refresh };
}

// Pure helper exported for tests.
export const __testInternals = { _summary };
