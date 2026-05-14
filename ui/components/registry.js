/**
 * Endpoints View — LLM endpoint registry with circuit breaker state.
 *
 * Strangler fig: the registry list + add-endpoint form have been migrated
 * to TypeScript primitives in src/views/endpoints/. Legacy renderers below
 * stay for the source-tree fallback (no Vite build) — they bail when the
 * TS view has mounted to avoid clobbering its DOM on store updates.
 */
import { store } from '../services/store.js';
import { api } from '../services/api.js';
import { toast } from '../services/toast.js';

let _tsMounted = false;

export async function fetchRegistry() {
    if (_tsMounted) return; // TS view drives its own polling.
    try {
        const data = await api.fetchRegistry();
        store.update({ registry: data });
    } catch {}
}

export function initRegistry() {
    fetchRegistry();

    // Take over with the TS view (registry table + add-endpoint form). Bare
    // path lets Vite resolve to the .ts source during build; the source-tree
    // fallback gets a 404 here and the legacy listeners stay live.
    import('../src/views/endpoints/index')
        .then(({ mountEndpointsView }) => {
            _tsMounted = true;
            mountEndpointsView(
                {
                    view: document.getElementById('view-endpoints'),
                    addToggle: document.getElementById('add-endpoint-toggle'),
                    registry: document.getElementById('registry-container'),
                    formHost: document.getElementById('add-endpoint-form-host'),
                },
                {
                    api: {
                        fetchRegistry: api.fetchRegistry,
                        addEndpoint: api.addEndpoint,
                        toggleEndpoint: api.toggleEndpoint,
                        deleteEndpoint: api.deleteEndpoint,
                        updatePriority: api.updatePriority,
                        resetCircuitBreaker: api.resetCircuitBreaker,
                    },
                    toast,
                    initial: store.state.registry || [],
                    poll: (fn, intervalMs) => store.poll(fn, intervalMs, 'endpoints'),
                }
            );
        })
        .catch(() => {
            // No TS chunk available — legacy listeners already wired below.
        });

    // Add endpoint form toggle
    const toggleBtn = document.getElementById('add-endpoint-toggle');
    const form = document.getElementById('add-endpoint-form');
    if (toggleBtn && form) {
        toggleBtn.addEventListener('click', () => form.classList.toggle('hidden'));
        document.getElementById('ep-cancel-btn')?.addEventListener('click', () => form.classList.add('hidden'));
    }

    // Add endpoint submit
    const addBtn = document.getElementById('ep-add-btn');
    if (addBtn) {
        const fields = [
            { input: 'ep-name', err: 'ep-name-err' },
            { input: 'ep-url', err: 'ep-url-err' },
        ];
        // Clear the error decoration as soon as the user types — lets the
        // alert text disappear without waiting for another submit round.
        fields.forEach((f) => {
            const el = document.getElementById(f.input);
            if (el) el.addEventListener('input', () => _clearFieldError(f.input, f.err));
        });

        addBtn.addEventListener('click', async () => {
            const id = document.getElementById('ep-name')?.value?.trim() || '';
            const url = document.getElementById('ep-url')?.value?.trim() || '';
            const provider = document.getElementById('ep-provider')?.value;
            const priority = document.getElementById('ep-priority')?.value || '0';
            const apiKey = document.getElementById('ep-api-key')?.value || '';
            const modelsRaw = document.getElementById('ep-models')?.value || '';
            const models = modelsRaw
                .split(',')
                .map((s) => s.trim())
                .filter(Boolean);

            let firstInvalid = null;
            // ID: non-empty, alphanumeric + dash/underscore only
            if (!id) {
                _setFieldError('ep-name', 'ep-name-err', 'Required.');
                firstInvalid = firstInvalid || 'ep-name';
            } else if (!/^[a-z0-9][a-z0-9_-]*$/i.test(id)) {
                _setFieldError(
                    'ep-name',
                    'ep-name-err',
                    'Use letters, digits, - or _ (must start with a letter or digit).'
                );
                firstInvalid = firstInvalid || 'ep-name';
            } else {
                _clearFieldError('ep-name', 'ep-name-err');
            }
            // URL: non-empty, must parse, must be http(s)
            if (!url) {
                _setFieldError('ep-url', 'ep-url-err', 'Required.');
                firstInvalid = firstInvalid || 'ep-url';
            } else {
                try {
                    const u = new URL(url);
                    if (u.protocol !== 'http:' && u.protocol !== 'https:') {
                        throw new Error('Only http:// and https:// are supported.');
                    }
                    _clearFieldError('ep-url', 'ep-url-err');
                } catch (err) {
                    _setFieldError('ep-url', 'ep-url-err', err.message || 'Not a valid URL.');
                    firstInvalid = firstInvalid || 'ep-url';
                }
            }

            if (firstInvalid) {
                document.getElementById(firstInvalid)?.focus();
                return;
            }
            addBtn.textContent = 'Adding...';
            addBtn.disabled = true;
            try {
                await api.addEndpoint({
                    id,
                    url,
                    provider,
                    priority: parseInt(priority),
                    models,
                    api_key: apiKey,
                });
                toast(`Endpoint "${id}" added`, 'success');
                form.classList.add('hidden');
                document.getElementById('ep-name').value = '';
                document.getElementById('ep-url').value = '';
                if (document.getElementById('ep-api-key')) document.getElementById('ep-api-key').value = '';
                if (document.getElementById('ep-models')) document.getElementById('ep-models').value = '';
                fields.forEach((f) => _clearFieldError(f.input, f.err));
                await fetchRegistry();
            } catch (e) {
                toast(`Failed: ${e.message}`, 'error');
            }
            addBtn.textContent = 'Add Endpoint';
            addBtn.disabled = false;
        });
    }
}

function _setFieldError(inputId, errId, msg) {
    const input = document.getElementById(inputId);
    const err = document.getElementById(errId);
    if (!input || !err) return;
    input.setAttribute('aria-invalid', 'true');
    input.classList.add('border-rose-500/50');
    input.classList.remove('border-white/10');
    err.textContent = msg;
    err.classList.remove('hidden');
}

function _clearFieldError(inputId, errId) {
    const input = document.getElementById(inputId);
    const err = document.getElementById(errId);
    if (!input || !err) return;
    input.removeAttribute('aria-invalid');
    input.classList.remove('border-rose-500/50');
    input.classList.add('border-white/10');
    err.classList.add('hidden');
    err.textContent = '';
}

const CIRCUIT_STATES = {
    closed: { label: 'CLOSED', dot: 'bg-emerald-400 shadow-emerald-500/40', text: 'text-emerald-400' },
    open: { label: 'OPEN', dot: 'bg-rose-400 shadow-rose-500/40 animate-pulse', text: 'text-rose-400' },
    half_open: { label: 'HALF', dot: 'bg-amber-400 shadow-amber-500/40', text: 'text-amber-400' },
};

let _sortKey = 'priority';
let _sortAsc = false;

function sortEndpoints(endpoints) {
    const sorted = [...endpoints];
    sorted.sort((a, b) => {
        let va = a[_sortKey] ?? '',
            vb = b[_sortKey] ?? '';
        if (typeof va === 'string') va = va.toLowerCase();
        if (typeof vb === 'string') vb = vb.toLowerCase();
        if (va < vb) return _sortAsc ? -1 : 1;
        if (va > vb) return _sortAsc ? 1 : -1;
        return 0;
    });
    return sorted;
}

export function renderRegistry() {
    if (_tsMounted) return; // TS view owns the registry container.
    const container = document.getElementById('registry-container');
    if (!container) return;

    const endpoints = sortEndpoints(store.state.registry || []);

    if (endpoints.length === 0) {
        container.innerHTML = `
            <div class="bg-gradient-to-br from-cyan-500/[0.06] to-violet-500/[0.06] backdrop-blur-xl rounded-2xl border border-cyan-500/20 p-10 text-center">
                <div class="flex items-center justify-center mb-4">
                    <div class="w-12 h-12 rounded-2xl bg-cyan-500/10 border border-cyan-500/30 flex items-center justify-center">
                        <svg class="w-6 h-6 text-cyan-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/></svg>
                    </div>
                </div>
                <h3 class="text-sm font-bold text-white mb-1">Welcome to LLMProxy</h3>
                <p class="text-[11px] text-slate-400 mb-5 max-w-md mx-auto">No endpoints yet. Add your first provider to start routing requests. The proxy is running in onboarding mode &mdash; inference calls will 503 until an endpoint is added.</p>
                <div class="flex items-center justify-center gap-2 mb-5">
                    <button id="onboarding-add-ep" class="bg-cyan-500/20 hover:bg-cyan-500/30 border border-cyan-500/40 text-cyan-300 text-[11px] font-bold px-4 py-2 rounded-lg transition-colors">
                        Add first endpoint
                    </button>
                </div>
                <details class="text-left max-w-lg mx-auto">
                    <summary class="text-[10px] text-slate-500 cursor-pointer hover:text-slate-300">Prefer env vars? (LM Studio, vLLM, Ollama)</summary>
                    <pre class="text-[10px] text-slate-400 mt-2 bg-black/30 rounded p-3 overflow-x-auto"><code># In .env then restart
LLM_PROXY_ENDPOINT_LOCAL_URL=http://192.168.1.50:1234/v1
LLM_PROXY_ENDPOINT_LOCAL_MODELS=llama-3.3-70b</code></pre>
                </details>
            </div>
        `;
        const onboardBtn = document.getElementById('onboarding-add-ep');
        if (onboardBtn) {
            onboardBtn.addEventListener('click', () => {
                const form = document.getElementById('add-endpoint-form');
                if (form) {
                    form.classList.remove('hidden');
                    form.scrollIntoView({ behavior: 'smooth', block: 'center' });
                }
                document.getElementById('ep-name')?.focus();
            });
        }
        return;
    }

    const sortCell = (key, label) => {
        const ariaSort = _sortKey === key ? (_sortAsc ? 'ascending' : 'descending') : 'none';
        const indicator = _sortKey === key ? (_sortAsc ? ' ▲' : ' ▼') : '';
        // The <th> carries aria-sort (ARIA grid pattern). The inner <button>
        // takes the click/Enter/Space, so keyboard users get the same affordance
        // as mouse users without custom tabindex/role plumbing.
        return `
            <th scope="col" aria-sort="${ariaSort}" class="text-left text-[9px] font-bold text-slate-500 uppercase tracking-widest px-4 py-3">
                <button type="button" data-sort="${key}" class="flex items-center gap-1 uppercase tracking-widest font-bold text-slate-500 hover:text-white transition-colors focus:border-cyan-500/50">
                    <span>${label}</span><span aria-hidden="true" class="text-[8px]">${indicator}</span>
                </button>
            </th>`;
    };

    container.innerHTML = `
        <div class="bg-white/[0.03] backdrop-blur-xl rounded-2xl border border-white/[0.06] overflow-x-auto">
            <table class="w-full min-w-[640px]">
                <thead>
                    <tr class="border-b border-white/[0.06]">
                        ${sortCell('id', 'Endpoint')}
                        ${sortCell('status', 'Status')}
                        ${sortCell('circuit_state', 'Circuit')}
                        ${sortCell('latency', 'Latency')}
                        ${sortCell('priority', 'Priority')}
                        <th scope="col" class="text-right text-[9px] font-bold text-slate-500 uppercase tracking-widest px-4 py-3">Actions</th>
                    </tr>
                </thead>
                <tbody id="registry-body"></tbody>
            </table>
        </div>
    `;

    const tbody = document.getElementById('registry-body');
    endpoints.forEach((ep) => {
        const statusColor = ep.status === 'Live' ? 'emerald' : ep.status === 'IGNORED' ? 'slate' : 'amber';
        const circuit = CIRCUIT_STATES[(ep.circuit_state || 'closed').toLowerCase()] || CIRCUIT_STATES.closed;

        const row = document.createElement('tr');
        row.className = 'border-b border-white/[0.04] hover:bg-white/[0.02] transition-colors';
        row.innerHTML = `
            <td class="px-4 py-3">
                <p class="text-[11px] font-bold text-white">${ep.name || ep.id}</p>
                <p class="text-[9px] text-slate-500 font-mono truncate max-w-xs">${ep.url}</p>
            </td>
            <td class="px-4 py-3">
                <span class="text-[9px] font-bold text-${statusColor}-400 bg-${statusColor}-500/10 px-2 py-0.5 rounded">${ep.status}</span>
            </td>
            <td class="px-4 py-3">
                <div class="flex items-center gap-1.5" data-explain="circuit:${ep.id}">
                    <div class="w-2 h-2 rounded-full ${circuit.dot} shadow-[0_0_6px]"></div>
                    <span class="text-[9px] font-bold font-mono ${circuit.text}">${circuit.label}</span>
                    ${(ep.failure_count || 0) > 0 ? `<span class="text-[10px] font-mono text-slate-600">${ep.failure_count}/${ep.failure_threshold || 5}</span>` : ''}
                </div>
            </td>
            <td class="px-4 py-3 text-[10px] font-mono text-slate-400">${ep.latency || '--'}</td>
            <td class="px-4 py-3">
                <div class="flex items-center gap-1">
                    <button data-action="priority-down" data-id="${ep.id}" class="text-slate-600 hover:text-white p-0.5 rounded hover:bg-white/5 transition-colors">
                        <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
                    </button>
                    <span class="text-[10px] font-mono text-slate-400 w-4 text-center">${ep.priority}</span>
                    <button data-action="priority-up" data-id="${ep.id}" class="text-slate-600 hover:text-white p-0.5 rounded hover:bg-white/5 transition-colors">
                        <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 15l7-7 7 7"/></svg>
                    </button>
                </div>
            </td>
            <td class="px-4 py-3 text-right">
                <button type="button" data-drilldown="endpoint:${ep.id}" class="text-[9px] text-slate-500 hover:text-cyan-400 px-2 py-1 rounded hover:bg-white/5 transition-colors">Inspect</button>
                <button type="button" data-action="reset-cb" data-id="${ep.id}" class="text-[9px] text-slate-500 hover:text-emerald-400 px-2 py-1 rounded hover:bg-white/5 transition-colors">Reset CB</button>
                <button type="button" data-action="toggle" data-id="${ep.id}" class="text-[9px] text-slate-500 hover:text-amber-400 px-2 py-1 rounded hover:bg-white/5 transition-colors">Toggle</button>
                <button type="button" data-action="delete" data-id="${ep.id}" class="text-[9px] text-slate-500 hover:text-rose-400 px-2 py-1 rounded hover:bg-white/5 transition-colors">Delete</button>
            </td>
        `;
        tbody.appendChild(row);
    });

    // Wire sort headers — <button> natively handles Enter/Space, so no
    // extra keydown handler is needed. Column becomes accessible for
    // keyboard users at zero custom cost.
    container.querySelectorAll('button[data-sort]').forEach((btn) => {
        btn.addEventListener('click', () => {
            const key = btn.dataset.sort;
            if (_sortKey === key) {
                _sortAsc = !_sortAsc;
            } else {
                _sortKey = key;
                _sortAsc = true;
            }
            renderRegistry();
        });
    });

    // Wire actions
    tbody.querySelectorAll('button[data-action]').forEach((btn) => {
        btn.addEventListener('click', async () => {
            const id = btn.dataset.id;
            const action = btn.dataset.action;
            try {
                if (action === 'reset-cb') {
                    await api.resetCircuitBreaker(id);
                    toast(`Circuit breaker ${id} reset to CLOSED`, 'success');
                } else if (action === 'toggle') {
                    await api.toggleEndpoint(id);
                    toast(`Endpoint ${id} toggled`, 'success');
                } else if (action === 'delete') {
                    const { confirm } = await import('../src/ui');
                    const ok = await confirm({
                        title: 'Delete endpoint',
                        message: `Remove "${id}" from the registry? Active traffic will be re-routed via the fallback chain.`,
                        confirmLabel: 'Delete',
                        danger: true,
                    });
                    if (!ok) return;
                    await api.deleteEndpoint(id);
                    toast(`Endpoint ${id} deleted`, 'success');
                } else if (action === 'priority-up') {
                    const ep = endpoints.find((e) => e.id === id);
                    if (ep) await api.updatePriority(id, (ep.priority || 0) + 1);
                } else if (action === 'priority-down') {
                    const ep = endpoints.find((e) => e.id === id);
                    if (ep) await api.updatePriority(id, Math.max(0, (ep.priority || 0) - 1));
                }
            } catch (e) {
                toast(`Action failed: ${e.message}`, 'error');
            }
            fetchRegistry();
        });
    });
}
