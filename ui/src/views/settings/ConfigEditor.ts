/**
 * Settings → Edit Configuration (admin-only).
 *
 * The one safe surface for changing the on-disk config.yaml *source* from the
 * UI. The flow is deliberately two-step so nothing risky is one click away:
 *
 *   1. Validate  → POST /api/v1/config/validate  (pure dry-run; errors+warnings)
 *   2. Apply     → confirm() → POST /api/v1/config/apply
 *                  (backend backs up, writes atomically, hot-reloads, audits;
 *                   an invalid config is rejected with 400 and never written)
 *
 * Edits target the on-disk source (env-ref based, no inline secrets) — NOT the
 * redacted runtime view in ConfigYaml. Apply is gated by a typed confirmation.
 */
import { confirm, createButton, createErrorState, createSkeleton } from '../../ui';

export interface ConfigEditorApi {
    fetchConfigRaw: () => Promise<{ yaml: string; path?: string }>;
    validateConfig: (yaml: string) => Promise<{ valid: boolean; errors: string[]; warnings: string[] }>;
    applyConfig: (yaml: string) => Promise<{ applied: boolean; warnings?: string[]; backup?: string }>;
}

export interface ConfigEditorHandle {
    refresh: () => Promise<void>;
}

type Toast = (m: string, k?: 'success' | 'error' | 'warning' | 'info') => void;

/** Pull the structured {errors,warnings} out of a 4xx thrown by api._fetch. */
function parseApplyError(err: any): { errors: string[]; warnings: string[] } {
    try {
        const body = JSON.parse(err?.body ?? '{}');
        const detail = body?.detail ?? {};
        if (Array.isArray(detail?.errors)) {
            return { errors: detail.errors, warnings: detail.warnings ?? [] };
        }
        if (typeof detail === 'string') return { errors: [detail], warnings: [] };
    } catch {
        /* fall through */
    }
    return { errors: [err?.message || 'Apply failed'], warnings: [] };
}

export function mountConfigEditor(host: HTMLElement, api: ConfigEditorApi, toast?: Toast): ConfigEditorHandle {
    const card = document.createElement('div');
    card.className = 'bg-white/[0.03] backdrop-blur-xl rounded-2xl border border-white/[0.06] p-6';
    card.setAttribute('data-testid', 'settings-config-editor');

    const head = document.createElement('div');
    head.className = 'flex items-center justify-between mb-3';
    const title = document.createElement('h2');
    title.className = 'text-xs font-bold text-white';
    title.textContent = 'Edit Configuration';
    head.appendChild(title);
    const note = document.createElement('span');
    note.className = 'text-[10px] text-slate-500 font-mono';
    note.textContent = 'admin · validated · backed up before apply';
    head.appendChild(note);
    card.appendChild(head);

    const body = document.createElement('div');
    card.appendChild(body);
    host.replaceChildren(card);

    const textarea = document.createElement('textarea');
    textarea.className =
        'w-full h-80 bg-black/40 border border-white/10 rounded-lg p-3 text-[11px] text-slate-200 ' +
        'font-mono leading-relaxed focus:border-sky-500/50 focus:outline-none resize-y';
    textarea.setAttribute('spellcheck', 'false');
    textarea.setAttribute('data-testid', 'config-editor-textarea');

    // Result strip (validation/apply feedback).
    const result = document.createElement('div');
    result.className = 'mt-3 font-mono text-[10px] min-h-[1rem]';

    function renderResult(kind: 'ok' | 'error' | 'warning' | 'info', lines: string[]): void {
        const tone =
            kind === 'ok' ? 'text-emerald-400'
            : kind === 'error' ? 'text-rose-400'
            : kind === 'warning' ? 'text-amber-400'
            : 'text-slate-400';
        result.className = `mt-3 font-mono text-[10px] min-h-[1rem] ${tone}`;
        result.replaceChildren();
        for (const line of lines) {
            const p = document.createElement('div');
            p.textContent = line;
            result.appendChild(p);
        }
    }

    const validateBtn = createButton({
        label: 'Validate',
        variant: 'secondary',
        size: 'sm',
        testId: 'config-validate-btn',
        onClick: async () => {
            renderResult('info', ['Validating…']);
            try {
                const res = await api.validateConfig(textarea.value);
                if (res.valid) {
                    renderResult('ok', [
                        '✓ Config is valid.',
                        ...(res.warnings || []).map((w) => `⚠ ${w}`),
                    ]);
                } else {
                    renderResult('error', (res.errors || ['Invalid config']).map((e) => `✗ ${e}`));
                }
            } catch (e: any) {
                renderResult('error', [`✗ ${e?.message || 'Validation request failed'}`]);
            }
        },
    });

    const applyBtn = createButton({
        label: 'Apply',
        variant: 'primary',
        size: 'sm',
        testId: 'config-apply-btn',
        onClick: async () => {
            // Validate first so the confirm dialog only appears for sane input.
            renderResult('info', ['Validating before apply…']);
            let preflight;
            try {
                preflight = await api.validateConfig(textarea.value);
            } catch (e: any) {
                renderResult('error', [`✗ ${e?.message || 'Validation request failed'}`]);
                return;
            }
            if (!preflight.valid) {
                renderResult('error', (preflight.errors || ['Invalid config']).map((e) => `✗ ${e}`));
                return;
            }
            const ok = await confirm({
                title: 'Apply configuration',
                message:
                    'This writes config.yaml and hot-reloads the proxy. The current config is ' +
                    'backed up first and automatically rolled back if the new one fails to load. Proceed?',
                confirmLabel: 'Write & reload',
                danger: true,
            });
            if (!ok) return;
            renderResult('info', ['Applying…']);
            try {
                const res = await api.applyConfig(textarea.value);
                renderResult('ok', [
                    `✓ Config applied${res.backup ? ` (backup: ${res.backup})` : ''}.`,
                    ...(res.warnings || []).map((w) => `⚠ ${w}`),
                ]);
                toast?.('Configuration applied and reloaded', 'success');
                await refresh();
            } catch (e: any) {
                const { errors, warnings } = parseApplyError(e);
                renderResult('error', [
                    ...errors.map((x) => `✗ ${x}`),
                    ...warnings.map((w) => `⚠ ${w}`),
                ]);
                toast?.('Config apply failed — nothing changed', 'error');
            }
        },
    });

    const revertBtn = createButton({
        label: 'Revert',
        variant: 'ghost',
        size: 'sm',
        testId: 'config-revert-btn',
        onClick: async () => {
            await refresh(); // re-fetch the on-disk source, discarding unsaved edits
            renderResult('info', ['Reverted to the on-disk config.']);
        },
    });

    const footer = document.createElement('div');
    footer.className = 'flex items-center gap-2 mt-3';
    footer.append(validateBtn, applyBtn, revertBtn);

    async function refresh(): Promise<void> {
        body.replaceChildren(createSkeleton({ repeat: 6 }));
        try {
            const { yaml } = await api.fetchConfigRaw();
            textarea.value = yaml || '';
            result.replaceChildren();
            result.className = 'mt-3 font-mono text-[10px] min-h-[1rem]';
            body.replaceChildren(textarea, footer, result);
        } catch (e: any) {
            body.replaceChildren(
                createErrorState({
                    title: 'Could not load configuration source.',
                    detail: e?.message,
                    onRetry: refresh,
                })
            );
        }
    }

    void refresh();
    return { refresh };
}
