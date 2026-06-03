import { createBadge, createCard, createEmptyState, createErrorState, createSkeleton } from '../../ui';
import { copyText } from '../../../services/file_actions.js';
import type { ExportStatus } from './types';

export interface ExportApi {
    fetchExportStatus: () => Promise<ExportStatus>;
    exportFileUrl?: (name: string) => string;
}

export interface DataExportOptions {
    toast?: (message: string, kind?: 'success' | 'error' | 'warning' | 'info') => void;
}

function field(label: string, value: string, testId?: string): HTMLElement {
    const wrap = document.createElement('div');
    const lab = document.createElement('label');
    lab.className = 'text-[9px] font-bold text-slate-500 uppercase tracking-widest mb-1 block';
    lab.textContent = label;
    const val = document.createElement('p');
    val.className = 'text-[10px] text-white font-mono';
    val.textContent = value;
    if (testId) val.setAttribute('data-testid', testId);
    wrap.appendChild(lab);
    wrap.appendChild(val);
    return wrap;
}

export function mountDataExport(host: HTMLElement, api: ExportApi, opts: DataExportOptions = {}): () => Promise<void> {
    const heading = document.createElement('h2');
    heading.className = 'text-xs font-bold text-white mb-4';
    heading.textContent = 'Data Export';

    const inner = document.createElement('div');
    inner.appendChild(createSkeleton({ shape: 'block', height: '5rem', ariaLabel: '' }));

    const body = document.createElement('div');
    body.appendChild(heading);
    body.appendChild(inner);

    host.replaceChildren(createCard({ body, testId: 'settings-export' }));

    async function refresh(): Promise<void> {
        try {
            const data = await api.fetchExportStatus();
            if (!data.enabled) {
                inner.replaceChildren(
                    createEmptyState({
                        title: 'Export disabled',
                        description:
                            'Set security.export.enabled=true in config.yaml to start writing audit batches to disk.',
                        testId: 'export-disabled',
                    })
                );
                return;
            }
            const wrap = document.createElement('div');
            const grid = document.createElement('div');
            grid.className = 'grid grid-cols-2 gap-4 mb-3';
            grid.appendChild(field('Output Dir', data.output_dir ?? '--', 'export-output-dir'));
            const optionsBlock = document.createElement('div');
            const lab = document.createElement('label');
            lab.className = 'text-[9px] font-bold text-slate-500 uppercase tracking-widest mb-1 block';
            lab.textContent = 'Options';
            optionsBlock.appendChild(lab);
            const optsRow = document.createElement('div');
            optsRow.className = 'flex items-center gap-2';
            optsRow.appendChild(
                createBadge({
                    label: data.scrub_pii ? 'PII Scrub: ON' : 'PII Scrub: OFF',
                    intent: data.scrub_pii ? 'success' : 'neutral',
                    size: 'sm',
                    testId: 'export-pii-badge',
                })
            );
            optsRow.appendChild(
                createBadge({
                    label: data.compress ? 'Compress: ON' : 'Compress: OFF',
                    intent: data.compress ? 'info' : 'neutral',
                    size: 'sm',
                    testId: 'export-compress-badge',
                })
            );
            optionsBlock.appendChild(optsRow);
            grid.appendChild(optionsBlock);
            wrap.appendChild(grid);

            const files = data.files ?? [];
            if (files.length > 0) {
                const tail = document.createElement('div');
                tail.className = 'space-y-1 pt-2 border-t border-white/[0.04]';
                const head = document.createElement('p');
                head.className = 'text-[10px] text-slate-600 uppercase font-bold mb-1';
                head.textContent = 'Recent Files';
                tail.appendChild(head);
                tail.setAttribute('data-testid', 'export-files');
                for (const f of files) {
                    const row = document.createElement('div');
                    row.className = 'flex flex-col sm:flex-row sm:items-center sm:justify-between gap-0.5 sm:gap-2';
                    const name = document.createElement('span');
                    name.className = 'text-[9px] font-mono text-slate-400';
                    name.textContent = f.name;
                    const size = document.createElement('span');
                    size.className = 'text-[10px] font-mono text-slate-600';
                    size.textContent = `${(f.size_bytes / 1024).toFixed(1)} KB`;
                    const actions = document.createElement('div');
                    actions.className = 'flex items-center gap-1';
                    const copyBtn = document.createElement('button');
                    copyBtn.type = 'button';
                    copyBtn.className = 'text-[9px] font-bold text-slate-500 hover:text-white px-2 py-1 rounded border border-white/10 hover:bg-white/5';
                    copyBtn.textContent = 'Copy path';
                    copyBtn.setAttribute('data-testid', `export-copy-${f.name}`);
                    copyBtn.addEventListener('click', async () => {
                        const fullPath = data.output_dir ? `${data.output_dir.replace(/\/$/, '')}/${f.name}` : f.name;
                        const ok = await copyText(fullPath);
                        opts.toast?.(ok ? `Copied ${f.name}` : `Copy failed for ${f.name}`, ok ? 'success' : 'error');
                    });
                    actions.appendChild(copyBtn);
                    if (api.exportFileUrl) {
                        const dl = document.createElement('a');
                        dl.href = api.exportFileUrl(f.name);
                        dl.download = f.name;
                        dl.className = 'text-[9px] font-bold text-slate-500 hover:text-white px-2 py-1 rounded border border-white/10 hover:bg-white/5';
                        dl.textContent = 'Download';
                        dl.setAttribute('data-testid', `export-download-${f.name}`);
                        actions.appendChild(dl);
                    }
                    row.appendChild(name);
                    const right = document.createElement('div');
                    right.className = 'flex items-center gap-2';
                    right.appendChild(size);
                    right.appendChild(actions);
                    row.appendChild(right);
                    tail.appendChild(row);
                }
                wrap.appendChild(tail);
            } else {
                const p = document.createElement('p');
                p.className = 'text-[9px] text-slate-600 font-mono mt-2';
                p.textContent = 'No export files yet';
                wrap.appendChild(p);
            }
            inner.replaceChildren(wrap);
        } catch (err) {
            inner.replaceChildren(
                createErrorState({
                    title: 'Export service unavailable',
                    description: 'Could not load /api/v1/export/status.',
                    detail: (err as Error)?.message,
                    onRetry: () => void refresh(),
                    testId: 'export-error',
                })
            );
        }
    }

    void refresh();
    return refresh;
}
