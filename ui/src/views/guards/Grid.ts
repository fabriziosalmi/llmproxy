import { createEmptyState, createErrorState, createSkeleton } from '../../ui';
import { rum } from '../../services/rum';
import { createGuardCard } from './GuardCard';
import { GUARDS } from './catalog';
import type { GuardsState } from './types';

export interface GuardsGridDeps {
    /** Async function that flips a guard. Resolves with the new enabled state. */
    toggleGuard: (key: string, next: boolean) => Promise<{ enabled: boolean }>;
    /** Toast helper for surface success / failure. Optional — falls back to console. */
    toast?: (message: string, kind?: 'success' | 'error' | 'warning' | 'info') => void;
}

/**
 * Mount the 8-card guards grid into a container. Re-renders the grid in
 * place on every state push and after a successful toggle, so external
 * scroll position survives.
 */
export function mountGuardsGrid(
    container: HTMLElement,
    initialState: GuardsState | null,
    deps: GuardsGridDeps
): {
    setState(next: GuardsState): void;
    setLoading(loading: boolean): void;
    setError(message: string | null, detail?: string): void;
} {
    let state: GuardsState | null = initialState;
    let loading = initialState === null;
    let errorMessage: string | null = null;
    let errorDetail: string | undefined;

    // The host element in index.html already carries the grid classes
    // (id="guards-grid" class="grid ..."). Wrapping the cards in ANOTHER
    // <div class="grid"> here nests grids: the outer grid treats the inner
    // grid as a single cell, so the cards rendered inside end up at ~1/4
    // of the available width and titles wrap to two lines. Use the host
    // directly as our grid root.
    const grid = container;
    if (!grid.classList.contains('grid')) {
        grid.className = `${grid.className} grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4`.trim();
    }
    grid.setAttribute('data-testid', 'guards-grid');

    function render(): void {
        if (errorMessage) {
            grid.replaceChildren(
                createErrorState({
                    title: 'Failed to load guard status',
                    description: errorMessage,
                    detail: errorDetail,
                    onRetry: () => {
                        errorMessage = null;
                        errorDetail = undefined;
                        loading = true;
                        render();
                    },
                    testId: 'guards-grid-error',
                })
            );
            return;
        }

        if (loading || state === null) {
            const cards = document.createDocumentFragment();
            for (let i = 0; i < GUARDS.length; i++) {
                const card = createSkeleton({ shape: 'block', height: '8rem', ariaLabel: '' });
                cards.appendChild(card);
            }
            grid.replaceChildren(cards);
            return;
        }

        if (GUARDS.length === 0) {
            grid.replaceChildren(
                createEmptyState({
                    title: 'No guards registered',
                    description: 'The catalog is empty — check ui/src/views/guards/catalog.ts.',
                    testId: 'guards-grid-empty',
                })
            );
            return;
        }

        const fragment = document.createDocumentFragment();
        for (const spec of GUARDS) {
            let enabled: boolean;
            let statusOverride: string | undefined;
            if (spec.key === 'firewall') {
                enabled = state.firewall.enabled !== false;
                statusOverride = enabled ? 'ALWAYS ON' : `OFF · ${state.firewall.disabled_reason ?? 'config'}`;
            } else if (spec.toggleable) {
                enabled = state.features[spec.key] !== false;
            } else {
                enabled = true;
            }

            const card = createGuardCard({
                spec,
                enabled,
                statusOverride,
                onToggle: spec.toggleable
                    ? async (next: boolean) => {
                          rum.action('guard_toggle', { guard: spec.key, next });
                          try {
                              const res = await deps.toggleGuard(spec.key, next);
                              if (state) {
                                  state = {
                                      ...state,
                                      features: { ...state.features, [spec.key]: res.enabled },
                                  };
                              }
                              render();
                              deps.toast?.(`${spec.name} ${res.enabled ? 'enabled' : 'disabled'}`, 'success');
                          } catch (err) {
                              const msg = (err as Error)?.message ?? String(err);
                              deps.toast?.(`${spec.name} toggle failed: ${msg}`, 'error');
                              // Revert UI by re-rendering with the previous state.
                              render();
                          }
                      }
                    : undefined,
            });
            fragment.appendChild(card);
        }
        grid.replaceChildren(fragment);
    }

    render();

    return {
        setState(next: GuardsState): void {
            state = next;
            loading = false;
            errorMessage = null;
            render();
        },
        setLoading(next: boolean): void {
            loading = next;
            render();
        },
        setError(message: string | null, detail?: string): void {
            errorMessage = message;
            errorDetail = detail;
            render();
        },
    };
}
