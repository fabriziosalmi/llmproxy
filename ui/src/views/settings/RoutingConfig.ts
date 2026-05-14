/**
 * Settings → Routing (Q.1)
 *
 * cost_weight slider — drives the smart router's bias toward cheaper
 * models. Backend K.1 already exposes GET /api/v1/routing/config and
 * POST /api/v1/routing/cost-weight; this is the consumer.
 *
 * Slider semantics (0.0 → 1.0):
 *   0.00–0.05  Performance  — pick by success² / latency only
 *   0.20–0.40  Smart        — moderate cost weighting (default 0.3)
 *   0.60–1.00  Cost-first   — strong bias toward cheaper models
 *
 * Three quick-set buttons jump to canonical points (0.0 / 0.3 / 0.8)
 * for operators who don't want to fiddle. The slider posts on
 * `change` (release), not `input` — moves don't spam the backend.
 */

import { createBadge, createButton, createErrorState, createSkeleton } from '../../ui';

export interface RoutingConfig {
    cost_weight: number;
    priority_mode: boolean;
    strategy: 'performance' | 'smart_weighted' | 'priority';
}

export interface RoutingConfigApi {
    fetchRoutingConfig: () => Promise<RoutingConfig>;
    setRoutingCostWeight: (cost_weight: number) => Promise<RoutingConfig>;
}

const QUICK_SETS = [
    { label: 'Performance', value: 0, desc: 'Ignore cost — pick by success² / latency only' },
    { label: 'Smart', value: 0.3, desc: 'Moderate cost weighting — the default' },
    { label: 'Cost-first', value: 0.8, desc: 'Strong bias toward cheaper models' },
] as const;

function _strategyIntent(strategy: RoutingConfig['strategy']): 'success' | 'info' | 'warning' {
    return strategy === 'priority' ? 'warning' : strategy === 'performance' ? 'info' : 'success';
}

export interface RoutingConfigHandle {
    refresh: () => Promise<void>;
}

export function mountRoutingConfig(
    host: HTMLElement,
    api: RoutingConfigApi,
    toast?: (m: string, k?: 'success' | 'error' | 'warning' | 'info') => void
): RoutingConfigHandle {
    const card = document.createElement('div');
    card.className = 'bg-white/[0.03] backdrop-blur-xl rounded-2xl border border-white/[0.06] p-6';
    card.setAttribute('data-testid', 'settings-routing-config');

    const head = document.createElement('div');
    head.className = 'flex items-center justify-between mb-3';
    const title = document.createElement('h2');
    title.className = 'text-xs font-bold text-white';
    title.textContent = 'Routing — Cost Weight';
    head.appendChild(title);
    const strategySlot = document.createElement('div');
    strategySlot.setAttribute('data-testid', 'routing-strategy');
    head.appendChild(strategySlot);
    card.appendChild(head);

    const body = document.createElement('div');
    body.appendChild(createSkeleton({ shape: 'block', height: '6rem', ariaLabel: '' }));
    card.appendChild(body);

    host.replaceChildren(card);

    function paint(cfg: RoutingConfig): void {
        // Strategy badge in the header
        strategySlot.replaceChildren(
            createBadge({
                label: cfg.strategy.replace('_', ' '),
                intent: _strategyIntent(cfg.strategy),
                size: 'sm',
                dot: true,
                pulse: cfg.strategy !== 'priority',
                testId: 'routing-strategy-badge',
            })
        );

        const wrap = document.createElement('div');
        wrap.className = 'space-y-4';

        // Numeric readout — current cost_weight value, big-monospace.
        const valueRow = document.createElement('div');
        valueRow.className = 'flex items-baseline gap-3';
        const big = document.createElement('span');
        big.className = 'text-2xl font-black font-mono text-white';
        big.textContent = cfg.cost_weight.toFixed(2);
        big.setAttribute('data-testid', 'cost-weight-value');
        valueRow.appendChild(big);
        const helper = document.createElement('span');
        helper.className = 'text-[10px] font-mono text-slate-500';
        helper.textContent = '0.00 = ignore cost · 1.00 = full bias to cheap';
        valueRow.appendChild(helper);
        wrap.appendChild(valueRow);

        // Native range input — accessibility for free.
        const sliderWrap = document.createElement('div');
        sliderWrap.className = 'flex items-center gap-3';
        const slider = document.createElement('input');
        slider.type = 'range';
        slider.min = '0';
        slider.max = '1';
        slider.step = '0.01';
        slider.value = String(cfg.cost_weight);
        slider.disabled = cfg.priority_mode; // priority overrides cost weighting
        slider.className = 'flex-1 accent-cyan-500';
        slider.setAttribute('data-testid', 'cost-weight-slider');
        slider.setAttribute('aria-label', 'Cost weight, 0 to 1');

        // Live value preview while dragging — only POST on `change` (release).
        slider.addEventListener('input', () => {
            big.textContent = Number(slider.value).toFixed(2);
        });
        let inflight = false;
        slider.addEventListener('change', async () => {
            const v = Number(slider.value);
            if (Number.isNaN(v) || inflight) return;
            inflight = true;
            slider.disabled = true;
            try {
                await api.setRoutingCostWeight(v);
                toast?.(`cost_weight → ${v.toFixed(2)}`, 'success');
                await refresh();
            } catch (err) {
                toast?.(`Failed: ${(err as Error)?.message ?? err}`, 'error');
                // Revert the visible big-number to the persisted value.
                big.textContent = cfg.cost_weight.toFixed(2);
                slider.value = String(cfg.cost_weight);
            } finally {
                slider.disabled = cfg.priority_mode;
                inflight = false;
            }
        });
        sliderWrap.appendChild(slider);
        wrap.appendChild(sliderWrap);

        // Quick-set buttons — preset positions on the slider.
        const quickRow = document.createElement('div');
        quickRow.className = 'flex flex-col sm:flex-row gap-2';
        quickRow.setAttribute('data-testid', 'cost-weight-quickset-row');
        for (const qs of QUICK_SETS) {
            const isActive = Math.abs(cfg.cost_weight - qs.value) < 0.005;
            const btn = createButton({
                label: `${qs.label} · ${qs.value.toFixed(2)}`,
                size: 'sm',
                variant: isActive ? 'primary' : 'ghost',
                testId: `cost-weight-quickset-${qs.label.toLowerCase().replace('-', '')}`,
                onClick: async () => {
                    if (isActive || cfg.priority_mode) return;
                    const el = btn as HTMLButtonElement;
                    el.disabled = true;
                    try {
                        await api.setRoutingCostWeight(qs.value);
                        toast?.(`Routing → ${qs.label} (${qs.value.toFixed(2)})`, 'success');
                        await refresh();
                    } catch (err) {
                        toast?.(`Failed: ${(err as Error)?.message ?? err}`, 'error');
                    } finally {
                        el.disabled = false;
                    }
                },
            });
            btn.classList.add('flex-1');
            quickRow.appendChild(btn);
        }
        wrap.appendChild(quickRow);

        // Active-preset description / priority-mode override notice.
        const note = document.createElement('p');
        note.className = 'text-[10px] text-slate-500 leading-relaxed';
        if (cfg.priority_mode) {
            note.textContent =
                'Priority Steering is ON — cost weight is not used until you turn it off ' +
                '(POST /api/v1/proxy/priority/toggle).';
            note.classList.add('text-amber-400/80');
        } else {
            const active = QUICK_SETS.find((q) => Math.abs(cfg.cost_weight - q.value) < 0.005);
            note.textContent = active
                ? active.desc
                : 'Custom value — between the named presets. Slider gives finer control.';
        }
        wrap.appendChild(note);

        body.replaceChildren(wrap);
    }

    async function refresh(): Promise<void> {
        try {
            const cfg = await api.fetchRoutingConfig();
            paint(cfg);
        } catch (err) {
            body.replaceChildren(
                createErrorState({
                    title: 'Could not load routing config',
                    description: 'GET /api/v1/routing/config failed.',
                    detail: (err as Error)?.message,
                    onRetry: () => void refresh(),
                    testId: 'routing-config-error',
                })
            );
            strategySlot.replaceChildren();
        }
    }

    void refresh();
    return { refresh };
}
