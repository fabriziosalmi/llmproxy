/**
 * Plugin card — one per registered plugin. Surfaces ring + timeout + fail
 * policy, runtime stats (calls / blocks / err% / avg ms), optional latency
 * percentiles, optional config schema, and three actions:
 * Inspect (drilldown), Toggle (enable/disable), Uninstall (confirm).
 */
import { createBadge, createButton, createCard, cx } from '../../ui';
import { rum } from '../../services/rum';
import type { Plugin, PluginStats, UiSchemaField } from './types';
import { ringIntent, ringLabel } from './types';

export interface PluginCardDeps {
    onToggle: (name: string, nextEnabled: boolean) => Promise<void>;
    onUninstall: (name: string) => Promise<void>;
    refresh: () => Promise<void> | void;
    toast?: (message: string, kind?: 'success' | 'error' | 'warning' | 'info') => void;
}

function statsRow(p: Plugin, stats: PluginStats | undefined): HTMLElement {
    const inv = stats?.invocations ?? 0;
    const errs = stats?.errors ?? 0;
    const blocks = stats?.blocks ?? 0;
    const avgLat = stats?.avg_latency_ms ?? 0;
    const errRate = inv > 0 ? ((errs / inv) * 100).toFixed(1) : '0.0';

    const wrap = document.createElement('div');
    wrap.className = 'grid grid-cols-4 gap-1 mt-2 pt-2 border-t border-white/[0.04]';

    const cell = (value: string, label: string, tone: string): HTMLElement => {
        const c = document.createElement('div');
        c.className = 'text-center';
        const v = document.createElement('p');
        v.className = cx('text-[10px] font-bold font-mono', tone);
        v.textContent = value;
        c.appendChild(v);
        const l = document.createElement('p');
        l.className = 'text-[9px] text-slate-600 uppercase';
        l.textContent = label;
        c.appendChild(l);
        return c;
    };

    wrap.appendChild(cell(inv.toLocaleString(), 'calls', inv > 0 ? 'text-white' : 'text-slate-600'));
    wrap.appendChild(cell(String(blocks), 'blocks', blocks > 0 ? 'text-rose-400' : 'text-slate-600'));
    wrap.appendChild(cell(`${errRate}%`, 'err', errs > 0 ? 'text-amber-400' : 'text-slate-600'));
    wrap.appendChild(cell(avgLat.toFixed(1), 'ms avg', avgLat > 100 ? 'text-amber-400' : 'text-slate-600'));
    wrap.setAttribute('data-testid', `plugin-stats-${p.name}`);
    return wrap;
}

function percentilesRow(stats: PluginStats | undefined): HTMLElement | null {
    const pct = stats?.latency_percentiles ?? {};
    const has = (pct.p50 ?? 0) > 0 || (pct.p95 ?? 0) > 0 || (pct.p99 ?? 0) > 0;
    if (!has) return null;
    const wrap = document.createElement('div');
    wrap.className = 'mt-2 pt-2 border-t border-white/[0.04] flex items-center justify-between';
    const label = document.createElement('span');
    label.className = 'text-[9px] text-slate-600 uppercase font-bold';
    label.textContent = 'Latency';
    wrap.appendChild(label);

    const right = document.createElement('div');
    right.className = 'flex items-center gap-2';
    const seg = (key: string, value: number, tone: string): HTMLElement => {
        const s = document.createElement('span');
        s.className = 'text-[9px] font-mono text-slate-500';
        const colored = document.createElement('span');
        colored.className = tone;
        colored.textContent = value.toFixed(1);
        s.append(`${key} `);
        s.appendChild(colored);
        return s;
    };
    right.appendChild(seg('P50', pct.p50 ?? 0, 'text-white'));
    right.appendChild(seg('P95', pct.p95 ?? 0, 'text-amber-400'));
    right.appendChild(seg('P99', pct.p99 ?? 0, 'text-rose-400'));
    wrap.appendChild(right);
    return wrap;
}

function configRow(schema: UiSchemaField[] | undefined): HTMLElement | null {
    if (!schema || !Array.isArray(schema) || schema.length === 0) return null;
    const wrap = document.createElement('div');
    wrap.className = 'mt-2 pt-2 border-t border-white/[0.04] space-y-1';

    const head = document.createElement('p');
    head.className = 'text-[9px] text-slate-600 uppercase font-bold mb-1';
    head.textContent = 'Config ';
    const note = document.createElement('span');
    note.className = 'text-slate-700 normal-case';
    note.textContent = '(read-only)';
    head.appendChild(note);
    wrap.appendChild(head);

    for (const f of schema) {
        const row = document.createElement('div');
        row.className = 'flex items-center justify-between';
        const k = document.createElement('span');
        k.className = 'text-[10px] text-slate-500';
        k.textContent = f.label ?? f.key;
        row.appendChild(k);
        const v = document.createElement('span');
        v.className = 'text-[10px] font-mono text-slate-400';
        v.textContent = f.default !== undefined ? String(f.default) : '--';
        row.appendChild(v);
        wrap.appendChild(row);
    }
    return wrap;
}

export function createPluginCard(plugin: Plugin, stats: PluginStats | undefined, deps: PluginCardDeps): HTMLElement {
    const enabled = plugin.enabled !== false;

    // Header row: name + ring/timeout meta on the left, version + dot on the right.
    const head = document.createElement('div');
    head.className = 'flex items-start justify-between mb-2';

    const left = document.createElement('div');
    left.className = 'flex-1 min-w-0';
    const title = document.createElement('h3');
    title.className = 'text-[11px] font-bold text-white truncate';
    title.textContent = plugin.name || 'Unknown';
    left.appendChild(title);

    const meta = document.createElement('div');
    meta.className = 'flex items-center gap-2 mt-1';
    meta.appendChild(
        createBadge({
            label: ringLabel(plugin.hook),
            intent: ringIntent(plugin.hook),
            size: 'sm',
            testId: `plugin-ring-${plugin.name}`,
        })
    );
    const timeoutSpan = document.createElement('span');
    timeoutSpan.className = 'text-[10px] font-mono text-slate-600';
    timeoutSpan.textContent = `${plugin.timeout_ms ?? 500}ms`;
    meta.appendChild(timeoutSpan);
    const failSpan = document.createElement('span');
    failSpan.className = 'text-[10px] font-mono text-slate-600';
    failSpan.textContent = plugin.fail_policy ?? 'open';
    meta.appendChild(failSpan);
    left.appendChild(meta);

    const right = document.createElement('div');
    right.className = 'flex items-center gap-1.5 shrink-0';
    if (plugin.version && plugin.version !== '0.0.0') {
        const v = document.createElement('span');
        v.className = 'text-[10px] font-mono text-slate-500';
        v.textContent = `v${plugin.version}`;
        right.appendChild(v);
    }
    const dot = document.createElement('div');
    // O.2: enabled dot pulses; disabled stays still. The pulse is the
    // cheapest "this is alive" cue — same animation token as the Live
    // status badges in the registry table.
    dot.className = cx(
        'w-2 h-2 rounded-full',
        enabled ? 'bg-emerald-400 pulse-live' : 'bg-slate-600',
    );
    dot.setAttribute('aria-label', enabled ? 'Enabled' : 'Disabled');
    right.appendChild(dot);

    head.appendChild(left);
    head.appendChild(right);

    // Body assembled out of order so we can drop in optional sections.
    const body = document.createElement('div');
    body.appendChild(head);

    if (plugin.description) {
        const desc = document.createElement('p');
        desc.className = 'text-[9px] text-slate-500 mb-2 line-clamp-2';
        desc.textContent = plugin.description;
        body.appendChild(desc);
    }

    body.appendChild(statsRow(plugin, stats));
    const percentiles = percentilesRow(stats);
    if (percentiles) body.appendChild(percentiles);
    const config = configRow(plugin.ui_schema);
    if (config) body.appendChild(config);

    // Actions row.
    const actions = document.createElement('div');
    actions.className = 'mt-2 pt-2 border-t border-white/[0.04] flex items-center justify-end gap-2';

    const inspect = createButton({
        label: 'Inspect',
        size: 'sm',
        variant: 'ghost',
        testId: `plugin-inspect-${plugin.name}`,
    });
    inspect.dataset.drilldown = `plugin:${plugin.name}`;
    actions.appendChild(inspect);

    const toggle = createButton({
        label: enabled ? 'Disable' : 'Enable',
        size: 'sm',
        variant: 'ghost',
        testId: `plugin-toggle-${plugin.name}`,
    });
    toggle.addEventListener('click', async () => {
        rum.action('plugin_toggle', { name: plugin.name, next: !enabled });
        try {
            await deps.onToggle(plugin.name, !enabled);
            deps.toast?.(`Plugin "${plugin.name}" ${enabled ? 'disabled' : 'enabled'}`, 'success');
            await deps.refresh();
        } catch (err) {
            deps.toast?.(`Toggle failed: ${(err as Error)?.message ?? err}`, 'error');
        }
    });
    actions.appendChild(toggle);

    const uninstall = createButton({
        label: 'Uninstall',
        size: 'sm',
        variant: 'ghost',
        testId: `plugin-uninstall-${plugin.name}`,
    });
    uninstall.addEventListener('click', async () => {
        const { confirm } = await import('../../ui');
        const ok = await confirm({
            title: 'Uninstall plugin',
            message: `Remove "${plugin.name}" from the pipeline? The proxy will hot-swap — in-flight requests finish through the old ring, new requests go through the new one.`,
            confirmLabel: 'Uninstall',
            danger: true,
        });
        if (!ok) return;
        rum.action('plugin_uninstall', { name: plugin.name });
        try {
            await deps.onUninstall(plugin.name);
            deps.toast?.(`Plugin "${plugin.name}" uninstalled`, 'success');
            await deps.refresh();
        } catch (err) {
            deps.toast?.(`Uninstall failed: ${(err as Error)?.message ?? err}`, 'error');
        }
    });
    actions.appendChild(uninstall);

    body.appendChild(actions);

    return createCard({
        body,
        // O.2: glow halo on enabled plugins — subtle emerald shadow that
        // reads as "this surface is healthy" without competing with the
        // glassmorphism. Disabled plugins stay flat + dimmed.
        className: cx('p-4', enabled ? 'glow-live' : 'opacity-50'),
        testId: `plugin-card-${plugin.name}`,
    });
}
