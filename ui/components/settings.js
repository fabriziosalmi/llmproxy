/**
 * Settings View — Identity, RBAC, webhooks, export, rate limiting, system info.
 */
import { api } from '../services/api.js';

export async function initSettings() {
    const tasks = [
        loadSystemInfo(),
        loadIdentity(),
        loadRbac(),
        loadWebhooks(),
        loadExport(),
    ];
    await Promise.allSettled(tasks);
}

async function loadSystemInfo() {
    try {
        const [version, info] = await Promise.all([
            api.fetchVersion(),
            api.fetchServiceInfo(),
        ]);
        setText('sys-version', version.version || '--');
        setText('sys-url', info.url || '--');
    } catch {}
}

async function loadIdentity() {
    try {
        const config = await fetch(`${window.location.origin}/api/v1/identity/config`).then(r => r.json());
        setText('auth-mode', config.enabled ? 'SSO / OIDC' : 'API Key');
        setText('sso-status', config.enabled ? 'Enabled' : 'Disabled');
    } catch {}

    try {
        const me = await api.fetchIdentityMe();
        const container = document.getElementById('identity-me');
        if (!container) return;
        if (me.authenticated) {
            container.innerHTML = `
                <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
                    <div>
                        <label class="text-[9px] font-bold text-slate-500 uppercase tracking-widest mb-1 block">Provider</label>
                        <p class="text-xs text-white font-mono">${me.provider || '--'}</p>
                    </div>
                    <div>
                        <label class="text-[9px] font-bold text-slate-500 uppercase tracking-widest mb-1 block">Email</label>
                        <p class="text-xs text-white font-mono truncate">${me.email || '--'}</p>
                    </div>
                    <div>
                        <label class="text-[9px] font-bold text-slate-500 uppercase tracking-widest mb-1 block">Roles</label>
                        <p class="text-xs font-mono">${(me.roles || []).map(r => `<span class="text-rose-400">${r}</span>`).join(', ') || '--'}</p>
                    </div>
                    <div>
                        <label class="text-[9px] font-bold text-slate-500 uppercase tracking-widest mb-1 block">Permissions</label>
                        <p class="text-[9px] text-slate-400 font-mono">${(me.permissions || []).length} granted</p>
                    </div>
                </div>
            `;
        } else {
            container.innerHTML = `<p class="text-[10px] text-slate-500 font-mono">Not authenticated</p>`;
        }
    } catch {
        const container = document.getElementById('identity-me');
        if (container) container.innerHTML = `<p class="text-[10px] text-slate-600 font-mono">Identity service unavailable</p>`;
    }
}

async function loadRbac() {
    const container = document.getElementById('rbac-matrix');
    if (!container) return;

    try {
        const roles = await api.fetchRbacRoles();
        const roleNames = Object.keys(roles);
        const allPerms = [...new Set(roleNames.flatMap(r => roles[r]))].sort();

        container.innerHTML = `
            <div class="overflow-x-auto">
                <table class="w-full">
                    <thead>
                        <tr class="border-b border-white/[0.06]">
                            <th class="text-left text-[10px] font-bold text-slate-500 uppercase px-2 py-1.5 sticky left-0 bg-[#050506]">Permission</th>
                            ${roleNames.map(r => `<th class="text-center text-[10px] font-bold text-slate-500 uppercase px-2 py-1.5">${r}</th>`).join('')}
                        </tr>
                    </thead>
                    <tbody>
                        ${allPerms.map(perm => `
                            <tr class="border-b border-white/[0.03] hover:bg-white/[0.02]">
                                <td class="text-[10px] font-mono text-slate-400 px-2 py-1 sticky left-0 bg-[#050506]">${perm}</td>
                                ${roleNames.map(r => `
                                    <td class="text-center px-2 py-1">
                                        ${roles[r].includes(perm) ? '<span class="text-emerald-400 text-[10px]">&#10003;</span>' : '<span class="text-slate-700 text-[10px]">-</span>'}
                                    </td>
                                `).join('')}
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            </div>
        `;
    } catch {
        container.innerHTML = `<p class="text-[10px] text-slate-600 font-mono">RBAC unavailable</p>`;
    }
}

async function loadWebhooks() {
    const container = document.getElementById('webhooks-status');
    if (!container) return;

    try {
        const data = await api.fetchWebhooks();
        if (!data.enabled) {
            container.innerHTML = `<p class="text-[10px] text-slate-600 font-mono">Webhooks disabled in config.yaml</p>`;
            return;
        }

        const eps = data.endpoints || [];
        const targetColors = { slack: 'violet', teams: 'sky', discord: 'indigo', generic: 'slate' };

        container.innerHTML = `
            <div class="space-y-2">
                ${eps.length === 0 ? '<p class="text-[10px] text-slate-600 font-mono">No endpoints configured</p>' :
                eps.map(ep => `
                    <div class="flex items-center justify-between p-2 bg-white/[0.02] rounded-lg">
                        <div class="flex items-center gap-2">
                            <span class="text-[10px] font-mono text-${targetColors[ep.target] || 'slate'}-400 bg-${targetColors[ep.target] || 'slate'}-500/10 px-1.5 py-0.5 rounded uppercase">${ep.target}</span>
                            <span class="text-[10px] font-bold text-white">${ep.name}</span>
                        </div>
                        <span class="text-[10px] font-mono text-slate-500">${ep.events.join(', ')}</span>
                    </div>
                `).join('')}
            </div>
            <div class="mt-3 pt-2 border-t border-white/[0.04]">
                <p class="text-[10px] text-slate-600 uppercase font-bold mb-1">Available Events</p>
                <div class="flex flex-wrap gap-1">
                    ${(data.event_types || []).map(e => `<span class="text-[9px] font-mono text-slate-500 bg-white/[0.03] px-1.5 py-0.5 rounded">${e}</span>`).join('')}
                </div>
            </div>
        `;
    } catch {
        container.innerHTML = `<p class="text-[10px] text-slate-600 font-mono">Webhook service unavailable</p>`;
    }
}

async function loadExport() {
    const container = document.getElementById('export-status');
    if (!container) return;

    try {
        const data = await api.fetchExportStatus();
        if (!data.enabled) {
            container.innerHTML = `<p class="text-[10px] text-slate-600 font-mono">Export disabled in config.yaml</p>`;
            return;
        }

        const files = data.files || [];
        container.innerHTML = `
            <div class="grid grid-cols-2 gap-4 mb-3">
                <div>
                    <label class="text-[9px] font-bold text-slate-500 uppercase tracking-widest mb-1 block">Output Dir</label>
                    <p class="text-[10px] text-white font-mono">${data.output_dir}</p>
                </div>
                <div>
                    <label class="text-[9px] font-bold text-slate-500 uppercase tracking-widest mb-1 block">Options</label>
                    <p class="text-[10px] text-white font-mono">PII Scrub: ${data.scrub_pii ? 'ON' : 'OFF'} | Compress: ${data.compress ? 'ON' : 'OFF'}</p>
                </div>
            </div>
            ${files.length > 0 ? `
                <div class="space-y-1 pt-2 border-t border-white/[0.04]">
                    <p class="text-[10px] text-slate-600 uppercase font-bold mb-1">Recent Files</p>
                    ${files.map(f => `
                        <div class="flex items-center justify-between">
                            <span class="text-[9px] font-mono text-slate-400">${f.name}</span>
                            <span class="text-[10px] font-mono text-slate-600">${(f.size_bytes / 1024).toFixed(1)} KB</span>
                        </div>
                    `).join('')}
                </div>
            ` : '<p class="text-[9px] text-slate-600 font-mono mt-2">No export files yet</p>'}
        `;
    } catch {
        container.innerHTML = `<p class="text-[10px] text-slate-600 font-mono">Export service unavailable</p>`;
    }
}

function setText(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
}

export function renderSettings() {}
