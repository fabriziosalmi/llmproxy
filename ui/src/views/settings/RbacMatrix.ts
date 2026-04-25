import { createBadge, createCard, createEmptyState, createErrorState, createSkeleton } from '../../ui';
import type { RbacRoles } from './types';

export interface RbacApi {
    fetchRbacRoles: () => Promise<RbacRoles>;
}

function buildMatrix(roles: RbacRoles): HTMLElement {
    const roleNames = Object.keys(roles);
    if (roleNames.length === 0) {
        return createEmptyState({
            title: 'No roles configured',
            description: 'Add roles to security.rbac.roles in config.yaml to populate this matrix.',
            testId: 'rbac-empty',
        });
    }
    const allPerms = Array.from(new Set(roleNames.flatMap((r) => roles[r] ?? []))).sort();

    const wrap = document.createElement('div');
    wrap.className = 'overflow-x-auto';

    const table = document.createElement('table');
    table.className = 'w-full';
    table.setAttribute('role', 'table');
    table.setAttribute('data-testid', 'rbac-matrix-table');

    const thead = document.createElement('thead');
    const headRow = document.createElement('tr');
    headRow.className = 'border-b border-white/[0.06]';
    const headPerm = document.createElement('th');
    headPerm.className =
        'text-left text-[10px] font-bold text-slate-500 uppercase px-2 py-1.5 sticky left-0 bg-[#050506]';
    headPerm.scope = 'col';
    headPerm.textContent = 'Permission';
    headRow.appendChild(headPerm);
    for (const r of roleNames) {
        const th = document.createElement('th');
        th.scope = 'col';
        th.className = 'text-center text-[10px] font-bold text-slate-500 uppercase px-2 py-1.5';
        th.textContent = r;
        headRow.appendChild(th);
    }
    thead.appendChild(headRow);
    table.appendChild(thead);

    const tbody = document.createElement('tbody');
    for (const perm of allPerms) {
        const tr = document.createElement('tr');
        tr.className = 'border-b border-white/[0.03] hover:bg-white/[0.02]';
        const td = document.createElement('td');
        td.className = 'text-[10px] font-mono text-slate-400 px-2 py-1 sticky left-0 bg-[#050506]';
        td.textContent = perm;
        tr.appendChild(td);
        for (const r of roleNames) {
            const cell = document.createElement('td');
            cell.className = 'text-center px-2 py-1';
            const has = (roles[r] ?? []).includes(perm);
            const span = document.createElement('span');
            span.className = has ? 'text-emerald-400 text-[10px]' : 'text-slate-700 text-[10px]';
            span.textContent = has ? '✓' : '–';
            span.setAttribute('aria-label', has ? `${r} has ${perm}` : `${r} does not have ${perm}`);
            cell.appendChild(span);
            tr.appendChild(cell);
        }
        tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    wrap.appendChild(table);

    // Footer count summary.
    const summary = document.createElement('p');
    summary.className = 'text-[9px] text-slate-600 mt-3 font-mono';
    summary.append(`${roleNames.length} roles · ${allPerms.length} permissions · `);
    const badge = createBadge({ label: 'live', intent: 'success', dot: true, size: 'sm' });
    summary.appendChild(badge);
    wrap.appendChild(summary);

    return wrap;
}

export function mountRbacMatrix(host: HTMLElement, api: RbacApi): () => Promise<void> {
    const heading = document.createElement('h2');
    heading.className = 'text-xs font-bold text-white mb-4';
    heading.textContent = 'RBAC Role Matrix';

    const inner = document.createElement('div');
    inner.appendChild(createSkeleton({ shape: 'block', height: '8rem', ariaLabel: '' }));

    const body = document.createElement('div');
    body.appendChild(heading);
    body.appendChild(inner);

    host.replaceChildren(createCard({ body, testId: 'settings-rbac' }));

    async function refresh(): Promise<void> {
        try {
            const roles = await api.fetchRbacRoles();
            inner.replaceChildren(buildMatrix(roles));
        } catch (err) {
            inner.replaceChildren(
                createErrorState({
                    title: 'RBAC unavailable',
                    description: 'Could not load /api/v1/rbac/roles.',
                    detail: (err as Error)?.message,
                    onRetry: () => void refresh(),
                    testId: 'rbac-error',
                })
            );
        }
    }

    void refresh();
    return refresh;
}
