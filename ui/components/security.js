/**
 * Security Events Component — SOC dashboard panel.
 *
 * Displays: ThreatLedger stats, audit chain verification,
 * GDPR controls, semantic corpus info, response signing status.
 */
import { api } from '../services/api.js';

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
        const data = await api.get('/api/v1/guards/status');
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
        const data = await api.get('/api/v1/gdpr/retention');
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
                const data = await api.get('/api/v1/audit/verify');
                if (data.valid) {
                    resultEl.textContent = `✓ Chain valid — ${data.verified} entries verified, 0 tampering detected`;
                    resultEl.className = 'font-mono text-[10px] text-emerald-400';
                    const statusEl = document.getElementById('sec-chain-status');
                    if (statusEl) { statusEl.textContent = 'VALID'; statusEl.className = 'text-2xl font-black text-emerald-400'; }
                } else {
                    resultEl.textContent = `✗ CHAIN BROKEN at entry #${data.broken_at} — ${data.error || 'tamper detected'}`;
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

    // GDPR Export
    const exportBtn = document.getElementById('sec-gdpr-export-btn');
    if (exportBtn) {
        exportBtn.addEventListener('click', async () => {
            const subjectInput = document.getElementById('sec-gdpr-subject');
            const resultEl = document.getElementById('sec-gdpr-result');
            if (!subjectInput || !resultEl) return;

            const subject = subjectInput.value.trim();
            if (!subject) { resultEl.textContent = 'Enter a subject ID'; resultEl.classList.remove('hidden'); return; }

            resultEl.classList.remove('hidden');
            resultEl.textContent = 'Exporting...';
            try {
                const data = await api.get(`/api/v1/gdpr/export/${encodeURIComponent(subject)}`);
                resultEl.textContent = `Exported ${data.record_count} records for "${subject}" at ${data.exported_at}`;
                resultEl.className = 'mt-3 font-mono text-[10px] text-emerald-400';
            } catch (e) {
                resultEl.textContent = `No data found for "${subject}"`;
                resultEl.className = 'mt-3 font-mono text-[10px] text-slate-500';
            }
        });
    }
}
