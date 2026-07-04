import { createSkeleton } from '../../ui';
import { settingsPanel } from './panel';
import type { ServiceInfo, VersionInfo } from './types';

export interface SystemInfoApi {
    fetchVersion: () => Promise<VersionInfo>;
    fetchServiceInfo: () => Promise<ServiceInfo>;
}

function makeField(label: string, valueId: string): HTMLElement {
    const wrap = document.createElement('div');
    const lab = document.createElement('label');
    lab.className = 'text-[9px] font-bold text-slate-500 uppercase tracking-widest mb-1 block';
    lab.textContent = label;
    const val = document.createElement('p');
    val.id = valueId;
    val.className = 'text-xs text-white font-mono';
    val.appendChild(createSkeleton({ width: '60%', height: '0.875rem', ariaLabel: '' }));
    wrap.appendChild(lab);
    wrap.appendChild(val);
    return wrap;
}

function setText(host: HTMLElement, valueId: string, text: string): void {
    const el = host.querySelector<HTMLElement>(`#${valueId}`);
    if (!el) return;
    el.replaceChildren();
    el.textContent = text;
}

export function mountSystemInfo(host: HTMLElement, api: SystemInfoApi): () => Promise<void> {
    const grid = document.createElement('div');
    grid.className = 'grid grid-cols-1 sm:grid-cols-2 gap-4';
    grid.appendChild(makeField('Version', 'sys-version'));
    grid.appendChild(makeField('Endpoint', 'sys-url'));

    host.replaceChildren(settingsPanel({ title: 'System Info', body: grid, testId: 'settings-system-info' }));

    async function refresh(): Promise<void> {
        try {
            const [version, info] = await Promise.all([api.fetchVersion(), api.fetchServiceInfo()]);
            setText(host, 'sys-version', version.version ?? '--');
            setText(host, 'sys-url', info.url ?? '--');
        } catch {
            setText(host, 'sys-version', 'unavailable');
            setText(host, 'sys-url', 'unavailable');
        }
    }

    void refresh();
    return refresh;
}
