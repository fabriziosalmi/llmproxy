import { createCard, createEmptyState, createSkeleton } from '../../ui';
import type { IdentityConfig, IdentityMe } from './types';

export interface IdentityApi {
    fetchIdentityConfig: () => Promise<IdentityConfig>;
    fetchIdentityMe: () => Promise<IdentityMe>;
}

function field(label: string, value: string, mono = true, tone = 'text-white'): HTMLElement {
    const wrap = document.createElement('div');
    const lab = document.createElement('label');
    lab.className = 'text-[9px] font-bold text-slate-500 uppercase tracking-widest mb-1 block';
    lab.textContent = label;
    const val = document.createElement('p');
    val.className = `text-xs ${tone} ${mono ? 'font-mono' : ''} truncate`;
    val.textContent = value;
    wrap.appendChild(lab);
    wrap.appendChild(val);
    return wrap;
}

function rolesField(roles: string[]): HTMLElement {
    const wrap = document.createElement('div');
    const lab = document.createElement('label');
    lab.className = 'text-[9px] font-bold text-slate-500 uppercase tracking-widest mb-1 block';
    lab.textContent = 'Roles';
    const val = document.createElement('p');
    val.className = 'text-xs font-mono';
    if (roles.length === 0) {
        val.textContent = '--';
    } else {
        roles.forEach((r, i) => {
            if (i > 0) val.append(', ');
            const span = document.createElement('span');
            span.className = 'text-rose-400';
            span.textContent = r;
            val.appendChild(span);
        });
    }
    wrap.appendChild(lab);
    wrap.appendChild(val);
    return wrap;
}

export function mountIdentity(host: HTMLElement, api: IdentityApi): () => Promise<void> {
    const heading = document.createElement('h2');
    heading.className = 'text-xs font-bold text-white mb-4';
    heading.textContent = 'Identity & Access';

    const summaryGrid = document.createElement('div');
    summaryGrid.className = 'grid grid-cols-1 md:grid-cols-2 gap-4 mb-4';
    summaryGrid.id = 'settings-identity-summary';
    summaryGrid.replaceChildren(field('Auth Mode', '…'), field('SSO Status', '…'));

    const meContainer = document.createElement('div');
    meContainer.className = 'pt-3 border-t border-white/[0.04]';
    meContainer.id = 'settings-identity-me';
    meContainer.appendChild(createSkeleton({ shape: 'block', height: '4rem', ariaLabel: '' }));

    const body = document.createElement('div');
    body.appendChild(heading);
    body.appendChild(summaryGrid);
    body.appendChild(meContainer);

    host.replaceChildren(createCard({ body, testId: 'settings-identity' }));

    async function refresh(): Promise<void> {
        // Auth mode + SSO status
        try {
            const cfg = await api.fetchIdentityConfig();
            summaryGrid.replaceChildren(
                field('Auth Mode', cfg.enabled ? 'SSO / OIDC' : 'API Key'),
                field('SSO Status', cfg.enabled ? 'Enabled' : 'Disabled')
            );
        } catch {
            summaryGrid.replaceChildren(field('Auth Mode', 'unknown'), field('SSO Status', 'unknown'));
        }

        // Authenticated user
        try {
            const me = await api.fetchIdentityMe();
            if (!me.authenticated) {
                meContainer.replaceChildren(
                    createEmptyState({
                        title: 'Not authenticated',
                        description: 'Sign in via the SSO provider or paste an API key on the login overlay.',
                        testId: 'identity-me-empty',
                    })
                );
                return;
            }
            const grid = document.createElement('div');
            grid.className = 'grid grid-cols-2 md:grid-cols-4 gap-4';
            grid.appendChild(field('Provider', me.provider ?? '--'));
            grid.appendChild(field('Email', me.email ?? '--'));
            grid.appendChild(rolesField(me.roles ?? []));
            grid.appendChild(field('Permissions', `${(me.permissions ?? []).length} granted`, true, 'text-slate-400'));
            grid.setAttribute('data-testid', 'identity-me');
            meContainer.replaceChildren(grid);
        } catch {
            const p = document.createElement('p');
            p.className = 'text-[10px] text-slate-600 font-mono';
            p.textContent = 'Identity service unavailable';
            meContainer.replaceChildren(p);
        }
    }

    void refresh();
    return refresh;
}
