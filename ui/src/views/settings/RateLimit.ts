/**
 * Settings → Rate Limit. P.1
 *
 * Surfaces what the global RateLimitMiddleware is currently serving and
 * lets the operator switch between strict / normal / relaxed presets at
 * runtime. Backend (N.6) handles the limiter mutation + bucket flush;
 * this view just plumbs the API.
 */

import { createBadge, createButton, createErrorState, createSkeleton, cx } from '../../ui';

export interface RateLimitConfig {
    enabled: boolean;
    preset: string | null;
    requests_per_minute: number;
    burst: number;
    presets: Record<string, { requests_per_minute: number; burst: number }>;
}

export interface RateLimitApi {
    fetchRateLimitConfig: () => Promise<RateLimitConfig>;
    setRateLimitPreset: (preset: string) => Promise<{ preset: string; requests_per_minute: number; burst: number }>;
}

const PRESET_ORDER = ['strict', 'normal', 'relaxed'] as const;

type PresetName = (typeof PRESET_ORDER)[number];

const PRESET_TONE: Record<PresetName, 'success' | 'info' | 'warning'> = {
    strict: 'success',
    normal: 'info',
    relaxed: 'warning',
};

const PRESET_DESC: Record<PresetName, string> = {
    strict: 'Tight cap — for staging or hostile networks',
    normal: 'Default — balanced for production traffic',
    relaxed: 'High ceiling — for trusted internal use',
};

export interface RateLimitHandle {
    refresh: () => Promise<void>;
}

export function mountRateLimit(
    host: HTMLElement,
    api: RateLimitApi,
    toast?: (m: string, k?: 'success' | 'error' | 'warning' | 'info') => void
): RateLimitHandle {
    const card = document.createElement('div');
    card.className = 'bg-white/[0.03] backdrop-blur-xl rounded-2xl border border-white/[0.06] p-6';
    card.setAttribute('data-testid', 'settings-rate-limit');

    // Header: title + live status badge
    const head = document.createElement('div');
    head.className = 'flex items-center justify-between mb-3';
    const title = document.createElement('h2');
    title.className = 'text-xs font-bold text-white';
    title.textContent = 'Rate Limit';
    head.appendChild(title);
    const statusSlot = document.createElement('div');
    statusSlot.setAttribute('data-testid', 'rate-limit-status');
    head.appendChild(statusSlot);
    card.appendChild(head);

    const body = document.createElement('div');
    body.appendChild(createSkeleton({ shape: 'block', height: '5rem', ariaLabel: '' }));
    card.appendChild(body);

    host.replaceChildren(card);

    let lastConfig: RateLimitConfig | null = null;

    function paint(cfg: RateLimitConfig): void {
        // Status badge: enabled state.
        statusSlot.replaceChildren(
            createBadge({
                label: cfg.enabled ? 'enabled' : 'disabled',
                intent: cfg.enabled ? 'success' : 'neutral',
                size: 'sm',
                dot: cfg.enabled,
                pulse: cfg.enabled,
                testId: 'rate-limit-enabled',
            })
        );

        const wrap = document.createElement('div');

        // Live numbers strip — what the middleware is actually serving.
        const live = document.createElement('div');
        live.className = 'grid grid-cols-3 gap-3 mb-4';
        live.setAttribute('data-testid', 'rate-limit-live');
        const cell = (label: string, value: string, testId: string): HTMLElement => {
            const c = document.createElement('div');
            c.className = 'bg-white/[0.02] border border-white/[0.04] rounded-lg px-3 py-2';
            c.setAttribute('data-testid', testId);
            const l = document.createElement('p');
            l.className = 'text-[9px] font-bold text-slate-500 uppercase tracking-widest mb-1';
            l.textContent = label;
            const v = document.createElement('p');
            v.className = 'text-sm font-black text-white font-mono';
            v.textContent = value;
            c.appendChild(l);
            c.appendChild(v);
            return c;
        };
        live.appendChild(cell('Req / min', String(cfg.requests_per_minute), 'rate-limit-rpm'));
        live.appendChild(cell('Burst +', String(cfg.burst), 'rate-limit-burst'));
        live.appendChild(cell('Preset', cfg.preset ?? 'custom', 'rate-limit-preset-current'));
        wrap.appendChild(live);

        // Preset picker — three buttons. Active one is filled, others ghost.
        const presetRow = document.createElement('div');
        presetRow.className = 'flex flex-col sm:flex-row gap-2 mb-3';
        presetRow.setAttribute('data-testid', 'rate-limit-preset-row');
        for (const name of PRESET_ORDER) {
            const def = cfg.presets[name];
            if (!def) continue;
            const isActive = cfg.preset === name;
            const btn = createButton({
                label: `${name} · ${def.requests_per_minute}/min +${def.burst}`,
                size: 'sm',
                variant: isActive ? 'primary' : 'ghost',
                testId: `rate-limit-preset-${name}`,
                onClick: async () => {
                    if (isActive) return; // already that preset
                    const el = btn as HTMLButtonElement;
                    el.disabled = true;
                    try {
                        await api.setRateLimitPreset(name);
                        toast?.(`Rate limit preset → ${name}`, 'success');
                        await refresh();
                    } catch (err) {
                        toast?.(`Failed: ${(err as Error)?.message ?? err}`, 'error');
                    } finally {
                        el.disabled = false;
                    }
                },
            });
            btn.classList.add('flex-1');
            presetRow.appendChild(btn);
        }
        wrap.appendChild(presetRow);

        // Description for the active preset
        const desc = document.createElement('p');
        desc.className = 'text-[10px] text-slate-500 leading-relaxed';
        const activeName = (PRESET_ORDER as readonly string[]).includes(cfg.preset ?? '')
            ? (cfg.preset as PresetName)
            : null;
        desc.textContent = activeName
            ? PRESET_DESC[activeName]
            : 'Custom — using config.yaml values. Pick a preset to apply runtime.';
        // Tone-coded color for the active preset
        if (activeName) {
            const tone = PRESET_TONE[activeName];
            desc.classList.add(
                tone === 'success' ? 'text-emerald-400/70' : tone === 'info' ? 'text-cyan-400/70' : 'text-amber-400/70'
            );
        }
        wrap.appendChild(desc);

        body.replaceChildren(wrap);
    }

    async function refresh(): Promise<void> {
        try {
            const cfg = await api.fetchRateLimitConfig();
            lastConfig = cfg;
            paint(cfg);
        } catch (err) {
            body.replaceChildren(
                createErrorState({
                    title: 'Could not load rate-limit config',
                    description: 'GET /api/v1/rate-limit/config failed.',
                    detail: (err as Error)?.message,
                    onRetry: () => void refresh(),
                    testId: 'rate-limit-error',
                })
            );
            statusSlot.replaceChildren();
        }
    }

    void refresh();
    void lastConfig; // keep reference for future "compare against config.yaml" feature
    return { refresh };
}

// Re-export for the cx-using consumers; nothing else uses it here yet.
void cx;
