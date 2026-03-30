/**
 * Security Events Component — SOC dashboard panel.
 *
 * Displays: ThreatLedger stats, audit chain verification,
 * GDPR controls, semantic corpus info, response signing status.
 */
import { api } from '../services/api.js';
import { toast } from '../services/toast.js';

let _initialized = false;

export async function renderSecurity() {
    if (!_initialized) {
        _initListeners();
        _initialized = true;
    }
    await Promise.allSettled([
        _loadGuardsStatus(),
        _loadRetention(),
        _loadCorpusStats(),
    ]);
}

// ── Data Loaders ──

async function _loadGuardsStatus() {
    try {
        const data = await api.fetchGuardsStatus();
        const shield = data?.security_shield || {};

        // Threat Ledger
        const ledger = shield.threat_ledger || {};
        const trackedEl = document.getElementById('sec-tracked-ips');
        if (trackedEl) trackedEl.textContent = ledger.tracked_ips ?? '—';

        // Response signing
        const sigEl = document.getElementById('sec-signing-status');
        if (sigEl) {
            const signing = data?.response_signing?.enabled;
            sigEl.textContent = signing ? 'ACTIVE' : 'OFF';
            sigEl.className = signing
                ? 'text-2xl font-black text-emerald-400'
                : 'text-2xl font-black text-slate-500';
        }
    } catch {
        // Guards endpoint may not expose all fields yet
    }
}

async function _loadRetention() {
    try {
        const token = localStorage.getItem('proxy_key') || '';
        const headers = token ? { 'Authorization': `Bearer ${token}` } : {};
        const res = await fetch(`${window.location.origin}/api/v1/gdpr/retention`, { headers });
        const data = res.ok ? await res.json() : null;
        const el = document.getElementById('sec-retention-info');
        if (el && data) {
            el.textContent = `${data.retention_days}d retention · ${data.legal_basis}`;
        }
    } catch {
        const el = document.getElementById('sec-retention-info');
        if (el) el.textContent = 'Not configured';
    }
}

async function _loadCorpusStats() {
    try {
        // Corpus stats not yet exposed via API — use static info
        const stats = { total_patterns: 60, categories: {
            override: 9, extraction: 8, hijack: 8, bypass: 6,
            multilingual: 7, delimiter: 5, social: 7, exfiltration: 4
        }};

        const countEl = document.getElementById('sec-corpus-patterns');
        if (countEl) countEl.textContent = stats.total_patterns;

        const container = document.getElementById('sec-corpus-categories');
        if (container) {
            container.innerHTML = Object.entries(stats.categories).map(([cat, count]) =>
                `<div class="bg-white/5 rounded-lg p-2 text-center">
                    <p class="text-sm font-bold text-white">${count}</p>
                    <p class="text-[9px] text-slate-500 uppercase">${cat}</p>
                </div>`
            ).join('');
        }
    } catch { /* non-critical */ }
}

// ── Helpers ──

async function _authJson(path) {
    const token = localStorage.getItem('proxy_key') || '';
    const headers = token ? { 'Authorization': `Bearer ${token}` } : {};
    const res = await fetch(`${window.location.origin}${path}`, { headers });
    if (!res.ok) throw new Error(`${res.status}`);
    return res.json();
}

// ── Event Listeners ──

function _initListeners() {
    // Verify audit chain
    const verifyBtn = document.getElementById('sec-verify-btn');
    if (verifyBtn) {
        verifyBtn.addEventListener('click', async () => {
            const resultEl = document.getElementById('sec-verify-result');
            if (!resultEl) return;
            resultEl.textContent = 'Verifying chain...';
            resultEl.className = 'font-mono text-[10px] text-slate-400';
            try {
                const data = await _authJson('/api/v1/audit/verify');
                if (data.valid) {
                    resultEl.textContent = `Chain valid — ${data.verified} entries verified, 0 tampering detected`;
                    resultEl.className = 'font-mono text-[10px] text-emerald-400';
                    const statusEl = document.getElementById('sec-chain-status');
                    if (statusEl) { statusEl.textContent = 'VALID'; statusEl.className = 'text-2xl font-black text-emerald-400'; }
                } else {
                    resultEl.textContent = `CHAIN BROKEN at entry #${data.broken_at} — ${data.error || 'tamper detected'}`;
                    resultEl.className = 'font-mono text-[10px] text-rose-400';
                    const statusEl = document.getElementById('sec-chain-status');
                    if (statusEl) { statusEl.textContent = 'BROKEN'; statusEl.className = 'text-2xl font-black text-rose-400'; }
                }
            } catch (e) {
                resultEl.textContent = `Error: ${e.message}`;
                resultEl.className = 'font-mono text-[10px] text-rose-400';
            }
        });
    }

    // GDPR Export — downloads JSON file (audit #18)
    const exportBtn = document.getElementById('sec-gdpr-export-btn');
    if (exportBtn) {
        exportBtn.addEventListener('click', async () => {
            const subjectInput = document.getElementById('sec-gdpr-subject');
            const resultEl = document.getElementById('sec-gdpr-result');
            if (!subjectInput || !resultEl) return;

            const subject = subjectInput.value.trim();
            if (!subject) { toast('Enter a subject ID', 'warning'); return; }

            resultEl.classList.remove('hidden');
            resultEl.textContent = 'Exporting...';
            resultEl.className = 'mt-3 font-mono text-[10px] text-slate-400';
            try {
                const data = await _authJson(`/api/v1/gdpr/export/${encodeURIComponent(subject)}`);
                // Trigger file download
                const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `dsar_${subject}_${new Date().toISOString().slice(0, 10)}.json`;
                a.click();
                URL.revokeObjectURL(url);
                const count = (data.audit?.length || 0) + (data.spend?.length || 0) + (data.roles?.length || 0);
                resultEl.textContent = `Downloaded ${count} records for "${subject}"`;
                resultEl.className = 'mt-3 font-mono text-[10px] text-emerald-400';
                toast(`DSAR export downloaded (${count} records)`, 'success');
            } catch (e) {
                resultEl.textContent = `No data found for "${subject}"`;
                resultEl.className = 'mt-3 font-mono text-[10px] text-slate-500';
            }
        });
    }

    // GDPR Erase
    const eraseBtn = document.getElementById('sec-gdpr-erase-btn');
    if (eraseBtn) {
        eraseBtn.addEventListener('click', async () => {
            const subjectInput = document.getElementById('sec-gdpr-subject');
            const resultEl = document.getElementById('sec-gdpr-result');
            if (!subjectInput || !resultEl) return;
            const subject = subjectInput.value.trim();
            if (!subject) { toast('Enter a subject ID first', 'warning'); return; }
            if (!confirm(`GDPR ERASE: Permanently delete ALL data for "${subject}"?\nThis cannot be undone.`)) return;
            resultEl.classList.remove('hidden');
            resultEl.textContent = 'Erasing...';
            resultEl.className = 'mt-3 font-mono text-[10px] text-slate-400';
            try {
                const token = localStorage.getItem('proxy_key') || '';
                const res = await fetch(`${window.location.origin}/api/v1/gdpr/erase/${encodeURIComponent(subject)}`, {
                    method: 'POST',
                    headers: token ? { 'Authorization': `Bearer ${token}` } : {},
                });
                const data = await res.json();
                if (res.ok) {
                    const total = (data.audit_deleted || 0) + (data.spend_deleted || 0) + (data.roles_deleted || 0);
                    resultEl.textContent = `Erased ${total} records for "${subject}"`;
                    resultEl.className = 'mt-3 font-mono text-[10px] text-emerald-400';
                    toast(`GDPR erase: ${total} records deleted`, 'success');
                } else {
                    resultEl.textContent = data.detail || 'Erase failed';
                    resultEl.className = 'mt-3 font-mono text-[10px] text-rose-400';
                }
            } catch (e) {
                resultEl.textContent = `Error: ${e.message}`;
                resultEl.className = 'mt-3 font-mono text-[10px] text-rose-400';
            }
        });
    }

    // GDPR Purge Expired
    const purgeBtn = document.getElementById('sec-gdpr-purge-btn');
    if (purgeBtn) {
        purgeBtn.addEventListener('click', async () => {
            const resultEl = document.getElementById('sec-gdpr-result');
            if (!resultEl) return;
            resultEl.classList.remove('hidden');
            resultEl.textContent = 'Purging expired records...';
            try {
                const token = localStorage.getItem('proxy_key') || '';
                const res = await fetch(`${window.location.origin}/api/v1/gdpr/purge`, {
                    method: 'POST',
                    headers: token ? { 'Authorization': `Bearer ${token}` } : {},
                });
                const data = await res.json();
                if (res.ok) {
                    const total = (data.audit_deleted || 0) + (data.spend_deleted || 0);
                    resultEl.textContent = `Purged ${total} expired records`;
                    resultEl.className = 'mt-3 font-mono text-[10px] text-emerald-400';
                    toast(`Retention purge: ${total} records removed`, 'success');
                } else {
                    resultEl.textContent = data.detail || 'Purge failed';
                    resultEl.className = 'mt-3 font-mono text-[10px] text-rose-400';
                }
            } catch (e) {
                resultEl.textContent = `Error: ${e.message}`;
                resultEl.className = 'mt-3 font-mono text-[10px] text-rose-400';
            }
        });
    }

    // Audit Log Query
    const auditBtn = document.getElementById('sec-audit-query-btn');
    if (auditBtn) {
        auditBtn.addEventListener('click', async () => {
            const resultsEl = document.getElementById('sec-audit-results');
            if (!resultsEl) return;
            resultsEl.innerHTML = '<p class="text-[10px] text-slate-500 font-mono">Loading...</p>';

            const params = {};
            const model = document.getElementById('audit-model')?.value?.trim();
            const key = document.getElementById('audit-key')?.value?.trim();
            const blocked = document.getElementById('audit-blocked')?.value;
            const limit = document.getElementById('audit-limit')?.value || '25';
            if (model) params.model = model;
            if (key) params.key_prefix = key;
            if (blocked && blocked !== '-1') params.blocked = blocked;
            params.limit = limit;

            try {
                const data = await _authJson(`/api/v1/audit?${new URLSearchParams(params)}`);
                const items = data.items || [];
                if (!items.length) {
                    resultsEl.innerHTML = '<p class="text-[10px] text-slate-600 font-mono">No entries found.</p>';
                    return;
                }
                resultsEl.innerHTML = `
                    <div class="text-[9px] text-slate-600 font-mono mb-2">${data.total || items.length} total entries (showing ${items.length})</div>
                    <table class="w-full">
                        <thead>
                            <tr class="border-b border-white/[0.06]">
                                <th class="text-left text-[9px] font-bold text-slate-500 uppercase px-2 py-1">Time</th>
                                <th class="text-left text-[9px] font-bold text-slate-500 uppercase px-2 py-1">Model</th>
                                <th class="text-left text-[9px] font-bold text-slate-500 uppercase px-2 py-1">Status</th>
                                <th class="text-left text-[9px] font-bold text-slate-500 uppercase px-2 py-1">Tokens</th>
                                <th class="text-left text-[9px] font-bold text-slate-500 uppercase px-2 py-1">Cost</th>
                                <th class="text-left text-[9px] font-bold text-slate-500 uppercase px-2 py-1">Blocked</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${items.map(r => {
                                const ts = r.ts ? new Date(r.ts * 1000).toLocaleString() : '--';
                                const blocked = r.blocked ? '<span class="text-rose-400">YES</span>' : '<span class="text-emerald-400">no</span>';
                                const cost = r.cost_usd ? `$${r.cost_usd.toFixed(4)}` : '--';
                                return `<tr class="border-b border-white/[0.03] hover:bg-white/[0.02]">
                                    <td class="px-2 py-1 text-[9px] font-mono text-slate-500">${ts}</td>
                                    <td class="px-2 py-1 text-[10px] font-mono text-white">${r.model || '--'}</td>
                                    <td class="px-2 py-1 text-[10px] font-mono ${r.status >= 400 ? 'text-rose-400' : 'text-emerald-400'}">${r.status || '--'}</td>
                                    <td class="px-2 py-1 text-[9px] font-mono text-slate-400">${r.prompt_tokens || 0}p+${r.completion_tokens || 0}c</td>
                                    <td class="px-2 py-1 text-[9px] font-mono text-amber-400">${cost}</td>
                                    <td class="px-2 py-1 text-[9px] font-mono">${blocked}</td>
                                </tr>`;
                            }).join('')}
                        </tbody>
                    </table>`;
            } catch (e) {
                resultsEl.innerHTML = `<p class="text-[10px] text-rose-400 font-mono">Error: ${e.message}</p>`;
            }
        });
    }
}
