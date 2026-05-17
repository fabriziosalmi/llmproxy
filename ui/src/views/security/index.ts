import { renderCorpus, renderRetentionInfo, renderSigningStatus, renderTrackedIps } from './SecuritySummary';
import { renderAuditEmpty, renderAuditError, renderAuditLoading, renderAuditTable } from './AuditResultsTable';
import {
    renderChainStatus,
    renderGdprError,
    renderGdprPending,
    renderGdprSuccess,
    renderGdprWarning,
    renderVerifyBroken,
    renderVerifyError,
    renderVerifyPending,
    renderVerifyValid,
} from './SecurityFeedback';

type SecurityDeps = {
    fetchGuardsStatus: () => Promise<any>;
    getToken: () => string;
    origin: string;
    toast: (msg: string, type?: 'success' | 'error' | 'warning' | 'info', duration?: number) => void;
    timerange: {
        sinceEpochMs: () => number | null;
        untilEpochMs: () => number | null;
        label: () => string;
    };
};

type SecurityTargets = {
    trackedIps: HTMLElement | null;
    signingStatus: HTMLElement | null;
    retentionInfo: HTMLElement | null;
    corpusPatterns: HTMLElement | null;
    corpusCategories: HTMLElement | null;
};

export async function renderSecuritySummary(deps: SecurityDeps, targets: SecurityTargets): Promise<void> {
    await Promise.allSettled([loadGuardsStatus(deps, targets), loadRetention(deps, targets), loadCorpus(targets)]);
}

let initialized = false;

export function initSecurityView(deps: SecurityDeps): void {
    if (initialized) return;
    initialized = true;
    wireVerify(deps);
    wireGdprExport(deps);
    wireGdprErase(deps);
    wireGdprPurge(deps);
    wireAuditQuery(deps);
}

export async function renderSecurityView(deps: SecurityDeps, targets: SecurityTargets): Promise<void> {
    await renderSecuritySummary(deps, targets);
}

async function loadGuardsStatus(deps: SecurityDeps, targets: SecurityTargets): Promise<void> {
    try {
        const data = await deps.fetchGuardsStatus();
        const shield = data?.security_shield || {};
        const ledger = shield.threat_ledger || {};
        renderTrackedIps(targets.trackedIps, ledger.tracked_ips ?? '—');
        renderSigningStatus(targets.signingStatus, !!data?.response_signing?.enabled);
    } catch {
        // non-critical
    }
}

async function loadRetention(deps: SecurityDeps, targets: SecurityTargets): Promise<void> {
    try {
        const token = deps.getToken();
        const headers: Record<string, string> = {};
        if (token) headers.Authorization = `Bearer ${token}`;
        const res = await fetch(`${deps.origin}/api/v1/gdpr/retention`, { headers });
        const data = res.ok ? await res.json() : null;
        if (data) renderRetentionInfo(targets.retentionInfo, `${data.retention_days}d retention · ${data.legal_basis}`);
        else renderRetentionInfo(targets.retentionInfo, 'Not configured');
    } catch {
        renderRetentionInfo(targets.retentionInfo, 'Not configured');
    }
}

async function loadCorpus(targets: SecurityTargets): Promise<void> {
    const stats = {
        total_patterns: 60,
        categories: {
            override: 9,
            extraction: 8,
            hijack: 8,
            bypass: 6,
            multilingual: 7,
            delimiter: 5,
            social: 7,
            exfiltration: 4,
        },
    };
    renderCorpus(targets.corpusPatterns, targets.corpusCategories, stats);
}

async function authJson(deps: SecurityDeps, path: string): Promise<any> {
    const token = deps.getToken();
    const headers: Record<string, string> = {};
    if (token) headers.Authorization = `Bearer ${token}`;
    const res = await fetch(`${deps.origin}${path}`, { headers });
    if (!res.ok) throw new Error(`${res.status}`);
    return res.json();
}

function wireVerify(deps: SecurityDeps): void {
    const verifyBtn = document.getElementById('sec-verify-btn');
    if (!verifyBtn) return;
    verifyBtn.addEventListener('click', async () => {
        const resultEl = document.getElementById('sec-verify-result');
        if (!resultEl) return;
        renderVerifyPending(resultEl);
        try {
            const data = await authJson(deps, '/api/v1/audit/verify');
            const statusEl = document.getElementById('sec-chain-status');
            if (data.valid) {
                renderVerifyValid(resultEl, data.verified);
                renderChainStatus(statusEl, true);
            } else {
                renderVerifyBroken(resultEl, data.broken_at, data.error);
                renderChainStatus(statusEl, false);
            }
        } catch (e: any) {
            renderVerifyError(resultEl, e?.message || 'Unknown error');
        }
    });
}

function wireGdprExport(deps: SecurityDeps): void {
    const exportBtn = document.getElementById('sec-gdpr-export-btn');
    if (!exportBtn) return;
    exportBtn.addEventListener('click', async () => {
        const subjectInput = document.getElementById('sec-gdpr-subject') as HTMLInputElement | null;
        const resultEl = document.getElementById('sec-gdpr-result');
        if (!subjectInput || !resultEl) return;
        const subject = subjectInput.value.trim();
        if (!subject) {
            deps.toast('Enter a subject ID', 'warning');
            return;
        }
        renderGdprPending(resultEl, 'Exporting...');
        try {
            const data = await authJson(deps, `/api/v1/gdpr/export/${encodeURIComponent(subject)}`);
            const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `dsar_${subject}_${new Date().toISOString().slice(0, 10)}.json`;
            a.click();
            URL.revokeObjectURL(url);
            const count = (data.audit?.length || 0) + (data.spend?.length || 0) + (data.roles?.length || 0);
            renderGdprSuccess(resultEl, `Downloaded ${count} records for "${subject}"`);
            deps.toast(`DSAR export downloaded (${count} records)`, 'success');
        } catch {
            renderGdprWarning(resultEl, `No data found for "${subject}"`);
        }
    });
}

function wireGdprErase(deps: SecurityDeps): void {
    const eraseBtn = document.getElementById('sec-gdpr-erase-btn');
    if (!eraseBtn) return;
    eraseBtn.addEventListener('click', async () => {
        const subjectInput = document.getElementById('sec-gdpr-subject') as HTMLInputElement | null;
        const resultEl = document.getElementById('sec-gdpr-result');
        if (!subjectInput || !resultEl) return;
        const subject = subjectInput.value.trim();
        if (!subject) {
            deps.toast('Enter a subject ID first', 'warning');
            return;
        }
        const { confirm } = await import('../../ui');
        const ok = await confirm({
            title: 'GDPR — right to erasure (Article 17)',
            message: `Permanently delete ALL data for "${subject}" across the audit ledger, cache, and threat intel. This operation is irreversible. Type the subject ID exactly as shown in audit logs.`,
            confirmLabel: 'Erase subject',
            danger: true,
        });
        if (!ok) return;
        renderGdprPending(resultEl, 'Erasing...');
        try {
            const token = deps.getToken();
            const headers: Record<string, string> = {};
            if (token) headers.Authorization = `Bearer ${token}`;
            const res = await fetch(`${deps.origin}/api/v1/gdpr/erase/${encodeURIComponent(subject)}`, {
                method: 'POST',
                headers,
            });
            const data = await res.json();
            if (res.ok) {
                const total = (data.audit_deleted || 0) + (data.spend_deleted || 0) + (data.roles_deleted || 0);
                renderGdprSuccess(resultEl, `Erased ${total} records for "${subject}"`);
                deps.toast(`GDPR erase: ${total} records deleted`, 'success');
            } else renderGdprError(resultEl, data.detail || 'Erase failed');
        } catch (e: any) {
            renderGdprError(resultEl, `Error: ${e?.message || 'Unknown error'}`);
        }
    });
}

function wireGdprPurge(deps: SecurityDeps): void {
    const purgeBtn = document.getElementById('sec-gdpr-purge-btn');
    if (!purgeBtn) return;
    purgeBtn.addEventListener('click', async () => {
        const resultEl = document.getElementById('sec-gdpr-result');
        if (!resultEl) return;
        renderGdprPending(resultEl, 'Purging expired records...');
        try {
            const token = deps.getToken();
            const headers: Record<string, string> = {};
            if (token) headers.Authorization = `Bearer ${token}`;
            const res = await fetch(`${deps.origin}/api/v1/gdpr/purge`, { method: 'POST', headers });
            const data = await res.json();
            if (res.ok) {
                const total = (data.audit_deleted || 0) + (data.spend_deleted || 0);
                renderGdprSuccess(resultEl, `Purged ${total} expired records`);
                deps.toast(`Retention purge: ${total} records removed`, 'success');
            } else renderGdprError(resultEl, data.detail || 'Purge failed');
        } catch (e: any) {
            renderGdprError(resultEl, `Error: ${e?.message || 'Unknown error'}`);
        }
    });
}

function wireAuditQuery(deps: SecurityDeps): void {
    const btn = document.getElementById('sec-audit-query-btn');
    if (!btn) return;
    btn.addEventListener('click', async () => {
        const resultsEl = document.getElementById('sec-audit-results');
        if (!resultsEl) return;
        renderAuditLoading(resultsEl);
        const model = (document.getElementById('audit-model') as HTMLInputElement | null)?.value?.trim();
        const key = (document.getElementById('audit-key') as HTMLInputElement | null)?.value?.trim();
        const blocked = (document.getElementById('audit-blocked') as HTMLSelectElement | null)?.value;
        const limit = (document.getElementById('audit-limit') as HTMLInputElement | null)?.value || '25';
        const params = new URLSearchParams();
        if (model) params.set('model', model);
        if (key) params.set('key_prefix', key);
        if (blocked && blocked !== '-1') params.set('blocked', blocked);
        params.set('limit', limit);
        try {
            const data = await authJson(deps, `/api/v1/audit?${params.toString()}`);
            const sinceMs = deps.timerange.sinceEpochMs();
            const untilMs = deps.timerange.untilEpochMs();
            let items = data.items || [];
            if (sinceMs != null) items = items.filter((r: any) => (r.ts || 0) * 1000 >= sinceMs);
            if (untilMs != null) items = items.filter((r: any) => (r.ts || 0) * 1000 <= untilMs);
            if (!items.length) {
                const suffix = sinceMs != null ? ` in ${deps.timerange.label()}` : '';
                renderAuditEmpty(resultsEl, suffix);
                return;
            }
            renderAuditTable(resultsEl, items, deps.timerange.label());
        } catch (e: any) {
            renderAuditError(resultsEl, e?.message || 'Unknown error');
        }
    });
}
