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
}
