/**
 * LLMPROXY — Unified App Interface
 * Real-time Data Integration & System Management
 */

const state = {
    isCollapsed: false,
    currentTab: 'dashboard',
    registry: [],
    proxyEnabled: true,
    priorityMode: false,
    features: {},
    logSource: null
};

const BASE_URL = window.location.origin;

async function fetchNetworkInfo() {
    try {
        const response = await fetch(`${BASE_URL}/api/v1/network/info`);
        const data = await response.json();
        const addressEl = document.getElementById('ts-address');
        const statusEl = document.getElementById('ts-status');
        const endpointEl = document.getElementById('service-endpoint');
        
        if (addressEl) addressEl.textContent = `${data.host}:${data.port}`;
        if (statusEl) statusEl.textContent = data.tailscale_active ? 'ENFORCED' : 'LOCAL';
        if (endpointEl) endpointEl.textContent = `${data.host}:${data.port}`;
    } catch (err) {
        console.error("Failed to fetch network info:", err);
    }
}

async function fetchVersion() {
    try {
        const response = await fetch(`${BASE_URL}/api/v1/version`);
        const data = await response.json();
        document.getElementById('app-version-badge').textContent = `v${data.version}`;
    } catch (e) { console.error("Version fetch failed", e); }
}

// --- NAVIGATION ---

function toggleSidebar() {
    state.isCollapsed = !state.isCollapsed;
    const sidebar = document.getElementById('sidebar');
    const icon = document.getElementById('toggle-icon-svg');
    
    if (state.isCollapsed) {
        sidebar.classList.add('collapsed');
        icon.innerHTML = '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 5l7 7-7 7M5 5l7 7-7 7"/>';
    } else {
        sidebar.classList.remove('collapsed');
        icon.innerHTML = '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 19l-7-7 7-7m8 14l-7-7 7-7"/>';
    }
}

function switchTab(tabId) {
    document.querySelectorAll('.content-view').forEach(view => view.classList.add('hidden'));
    document.getElementById(`view-${tabId}`).classList.remove('hidden');
    
    document.querySelectorAll('.nav-item').forEach(item => {
        item.classList.remove('active');
        item.classList.add('text-slate-500');
    });
    const targetNav = document.getElementById(`nav-${tabId}`);
    if (targetNav) {
        targetNav.classList.add('active');
        targetNav.classList.remove('text-slate-500');
    }
    
    document.getElementById('view-title').firstChild.textContent = tabId.charAt(0).toUpperCase() + tabId.slice(1);
    state.currentTab = tabId;

    if (tabId === 'registry') fetchRegistry();
}

// --- REGISTRY & ACTIONS ---

async function fetchRegistry() {
    try {
        const response = await fetch(`${BASE_URL}/api/v1/registry`);
        const data = await response.json();
        state.registry = data;
        renderRegistry();
    } catch (error) {
        console.error("Failed to fetch registry:", error);
    }
}

async function toggleEndpoint(id) {
    try {
        await fetch(`${BASE_URL}/api/v1/registry/${id}/toggle`, { method: 'POST' });
        fetchRegistry();
    } catch (e) { console.error(e); }
}

async function deleteEndpoint(id) {
    if (!confirm(`Delete immutable endpoint ${id}?`)) return;
    try {
        await fetch(`${BASE_URL}/api/v1/registry/${id}`, { method: 'DELETE' });
        fetchRegistry();
    } catch (e) { console.error(e); }
}

async function updatePriority(id, val) {
    try {
        await fetch(`${BASE_URL}/api/v1/registry/${id}/priority`, {
            method: 'POST',
            body: JSON.stringify({ priority: parseInt(val) }),
            headers: { 'Content-Type': 'application/json' }
        });
        fetchRegistry();
    } catch (e) { console.error(e); }
}

async function togglePriorityMode() {
    const nextState = !state.priorityMode;
    try {
        const response = await fetch(`${BASE_URL}/api/v1/proxy/priority/toggle`, {
            method: 'POST',
            body: JSON.stringify({ enabled: nextState }),
            headers: { 'Content-Type': 'application/json' }
        });
        const data = await response.json();
        state.priorityMode = data.enabled;
        updatePriorityUI();
    } catch (e) { console.error(e); }
}

function updatePriorityUI() {
    const btn = document.getElementById('priority-mode-btn');
    const dot = btn.querySelector('div');
    if (state.priorityMode) {
        btn.classList.replace('bg-slate-800', 'bg-indigo-600');
        dot.classList.replace('bg-slate-500', 'bg-white');
        dot.style.transform = 'translateX(20px)';
    } else {
        btn.classList.replace('bg-indigo-600', 'bg-slate-800');
        dot.classList.replace('bg-white', 'bg-slate-500');
        dot.style.transform = 'translateX(0)';
    }
}

function renderRegistry() {
    const tbody = document.getElementById('registry-table-body');
    if (!tbody) return;
    tbody.innerHTML = '';
    
    state.registry.forEach(item => {
        const tr = document.createElement('tr');
        tr.className = 'group hover:bg-white/[0.02] transition-colors';
        const isLive = item.status === 'Live' || item.status === 'verified';
        const color = isLive ? 'text-emerald-400' : 'text-slate-600';
        const dot = isLive ? 'bg-emerald-500' : 'bg-slate-700';
        
        tr.innerHTML = `
            <td class="p-5">
                <div class="flex items-center gap-3">
                    <div class="w-1.5 h-1.5 rounded-full ${dot} ${isLive ? 'animate-pulse' : ''}"></div>
                    <span class="text-xs font-bold text-white tracking-tight">${item.name}</span>
                </div>
            </td>
            <td class="p-5 text-center">
                <input type="number" value="${item.priority}" onchange="updatePriority('${item.id}', this.value)" 
                    class="w-10 bg-white/5 border border-white/10 rounded-lg text-[10px] text-center font-bold text-sky-400 focus:border-sky-500 outline-none">
            </td>
            <td class="p-5 text-[10px] font-mono text-slate-500 font-medium">${item.type}</td>
            <td class="p-5 text-[10px] tabular-nums font-black text-sky-400">${item.latency}</td>
            <td class="p-5"><span class="text-[8px] font-black uppercase tracking-widest px-2 py-1 rounded bg-white/5 ${color} border border-white/5">${item.status}</span></td>
            <td class="p-5 text-right space-x-2">
                <button onclick="toggleEndpoint('${item.id}')" class="p-2 hover:bg-white/5 rounded-lg text-slate-500 hover:text-white transition-all" title="Toggle Endpoint">
                    <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/></svg>
                </button>
                <button onclick="deleteEndpoint('${item.id}')" class="p-2 hover:bg-red-500/10 rounded-lg text-slate-700 hover:text-red-400 transition-all" title="Delete">
                    <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
                </button>
            </td>
        `;
        tbody.appendChild(tr);
    });
}

// --- PROXY CONTROL ---

async function fetchProxyStatus() {
    try {
        const response = await fetch(`${BASE_URL}/api/v1/proxy/status`);
        const data = await response.json();
        state.proxyEnabled = data.enabled;
        state.priorityMode = data.priority_mode || false;
        updateProxyUI();
        updatePriorityUI();
    } catch (e) {
        console.error("Failed to fetch proxy status", e);
    }
}

async function toggleProxyService() {
    const nextState = !state.proxyEnabled;
    try {
        const response = await fetch(`${BASE_URL}/api/v1/proxy/toggle`, {
            method: 'POST',
            body: JSON.stringify({ enabled: nextState }),
            headers: { 'Content-Type': 'application/json' }
        });
        const data = await response.json();
        state.proxyEnabled = data.enabled;
        updateProxyUI();
    } catch (e) {
        console.error("Proxy toggle failed", e);
    }
}

function updateProxyUI() {
    const btn = document.getElementById('proxy-toggle-btn');
    const indicator = document.getElementById('proxy-status-indicator');
    const dot = btn.querySelector('div');
    if (state.proxyEnabled) {
        btn.classList.replace('bg-slate-700', 'bg-sky-500');
        dot.style.transform = 'translateX(32px)';
        indicator.querySelector('span:last-child').className = "text-emerald-400 font-mono text-xs font-black uppercase tracking-tighter";
        indicator.querySelector('span:last-child').textContent = "ACTIVE";
    } else {
        btn.classList.replace('bg-sky-500', 'bg-slate-700');
        dot.style.transform = 'translateX(0)';
        indicator.querySelector('span:last-child').className = "text-red-400 font-mono text-xs font-black uppercase tracking-tighter";
        indicator.querySelector('span:last-child').textContent = "STOPPED";
    }
}

function connectToLogs() {
    if (state.logSource) state.logSource.close();
    state.logSource = new EventSource(`${BASE_URL}/api/v1/logs`);
    state.logSource.onmessage = (event) => {
        const entry = JSON.parse(event.data);
        appendLog(entry);
    };
}

function appendLog(log) {
    const container = document.getElementById('terminal-logs');
    if (!container) return;
    if (container.children.length === 1 && container.children[0].classList.contains('italic')) container.innerHTML = '';
    const div = document.createElement('div');
    div.className = "flex gap-4 animate-in fade-in slide-in-from-left-1";
    const levelColor = log.level === 'PROXY' ? 'text-sky-400 font-black' : log.level === 'ERROR' ? 'text-red-400' : log.level === 'SYSTEM' ? 'text-amber-400' : 'text-slate-500';
    div.innerHTML = `
        <span class="text-slate-600 shrink-0">${log.timestamp}</span>
        <span class="w-12 tracking-widest text-[8px] uppercase font-black ${levelColor} shrink-0">${log.level}</span>
        <span class="text-slate-300 break-all">${log.message}</span>
    `;
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
    if (container.children.length > 50) container.removeChild(container.firstChild);
}

// --- CHAT ---

async function sendUserMessage() {
    const input = document.getElementById('chat-input');
    const scroller = document.getElementById('chat-scroller');
    const text = input.value.trim();
    if (!text) return;
    input.value = '';
    const userDiv = document.createElement('div');
    userDiv.className = 'flex justify-end gap-4 animate-in fade-in slide-in-from-right-2';
    userDiv.innerHTML = `<div class="p-5 bg-sky-500 border border-sky-400 rounded-3xl rounded-tr-sm text-sm leading-relaxed max-w-[85%] text-white shadow-2xl shadow-sky-500/20">${text}</div>`;
    scroller.appendChild(userDiv);
    scroller.scrollTop = scroller.scrollHeight;
    const thinkingDiv = document.createElement('div');
    thinkingDiv.className = 'flex gap-5 items-start animate-pulse';
    thinkingDiv.innerHTML = `<div class="w-9 h-9 rounded-2xl bg-white/5 border border-white/10 flex items-center justify-center text-slate-500 font-bold text-xs shrink-0">...</div>`;
    scroller.appendChild(thinkingDiv);
    scroller.scrollTop = scroller.scrollHeight;
    try {
        const response = await fetch(`${BASE_URL}/v1/chat/completions`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer sk-proxy-master-key-123' },
            body: JSON.stringify({ model: 'auto', messages: [{ role: 'user', content: text }] })
        });
        const data = await response.json();
        scroller.removeChild(thinkingDiv);
        const botDiv = document.createElement('div');
        botDiv.className = 'flex gap-5 items-start animate-in fade-in slide-in-from-left-2';
        if (response.status === 200) {
            botDiv.innerHTML = `<div class="w-9 h-9 rounded-2xl bg-emerald-500/10 border border-emerald-500/30 flex items-center justify-center text-emerald-400 font-bold text-xs shrink-0">P</div>
                <div class="p-5 bg-white/[0.03] border border-white/5 rounded-3xl rounded-tl-sm text-sm leading-relaxed max-w-[85%] text-slate-200">${data.choices[0].message.content}</div>`;
        } else {
            botDiv.innerHTML = `<div class="w-9 h-9 rounded-2xl bg-red-500/10 border border-red-500/30 flex items-center justify-center text-red-400 font-bold text-xs shrink-0">!</div>
                <div class="p-5 bg-red-500/5 border border-red-500/20 rounded-3xl rounded-tl-sm text-sm leading-relaxed max-w-[85%] text-red-200">${data.detail || 'Access Denied'}</div>`;
        }
        scroller.appendChild(botDiv);
        scroller.scrollTop = scroller.scrollHeight;
    } catch (e) {
        scroller.removeChild(thinkingDiv);
    }
}

// --- ANALYTICS ---

function initCharts() {
    const ctx = document.getElementById('mainChart');
    if (!ctx) return;
    new Chart(ctx.getContext('2d'), {
        type: 'line',
        data: {
            labels: Array.from({length: 12}, (_, i) => `${i*2}h`),
            datasets: [{
                data: [0.4, 0.45, 0.38, 0.42, 0.5, 0.41, 0.39, 0.35, 0.42, 0.48, 0.45, 0.42],
                borderColor: '#007aff',
                borderWidth: 2,
                tension: 0.4,
                fill: true,
                backgroundColor: 'rgba(0, 122, 255, 0.05)',
                pointRadius: 0
            }]
        },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } },
            scales: { x: { grid: { display: false }, ticks: { color: '#48484a', font: { size: 9 } } },
                      y: { grid: { color: 'rgba(255,255,255,0.02)' }, ticks: { color: '#48484a', font: { size: 9 } } } }
        }
    });
}

async function fetchServiceInfo() {
    try {
        const response = await fetch(`${BASE_URL}/api/v1/service-info`);
        const data = await response.json();
        document.getElementById('service-endpoint').textContent = `${data.host}:${data.port}`;
    } catch (e) { console.error(e); }
}

async function fetchFeatures() {
    try {
        const response = await fetch(`${BASE_URL}/api/v1/features`);
        state.features = await response.json();
        renderFeatures();
    } catch (e) { console.error(e); }
}

async function toggleFeature(name) {
    try {
        const response = await fetch(`${BASE_URL}/api/v1/features/toggle`, {
            method: 'POST',
            body: JSON.stringify({ name: name, enabled: !state.features[name] }),
            headers: { 'Content-Type': 'application/json' }
        });
        const data = await response.json();
        state.features[name] = data.enabled;
        renderFeatures();
    } catch (e) { console.error(e); }
}

function renderFeatures() {
    const container = document.getElementById('feature-toggles');
    const settingsContainer = document.querySelector('#view-settings .space-y-4');
    
    if (!container) return;
    container.innerHTML = '';
    
    if (settingsContainer) {
        settingsContainer.innerHTML = '';
    }
    
    Object.entries(state.features).forEach(([name, enabled]) => {
        // Injected in Proxy View
        const btn = document.createElement('button');
        btn.onclick = () => toggleFeature(name);
        btn.className = `flex items-center gap-2 px-3 py-1 rounded-full border text-[8px] font-black uppercase tracking-widest transition-all ${enabled ? 'bg-sky-500/10 border-sky-500/30 text-sky-400 font-bold' : 'bg-white/5 border-white/10 text-slate-600'}`;
        btn.innerHTML = `
            <div class="w-1.5 h-1.5 rounded-full ${enabled ? 'bg-sky-400 animate-pulse' : 'bg-slate-700'}"></div>
            ${name.replace(/_/g, ' ')}
        `;
        container.appendChild(btn);

        // Injected in Settings View
        if (settingsContainer) {
            const item = document.createElement('div');
            item.className = "glass p-6 rounded-3xl border border-white/5 flex items-center justify-between group hover:bg-white/[0.04] transition-all";
            item.innerHTML = `
                <div class="flex gap-5 items-center">
                    <div class="p-3 bg-white/5 rounded-2xl text-slate-400 group-hover:text-sky-400 transition-colors">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/>
                        </svg>
                    </div>
                    <div>
                        <h4 class="text-sm font-bold text-white mb-0.5 uppercase tracking-tight">${name.replace(/_/g, ' ')}</h4>
                        <p class="text-[10px] text-slate-500">Autonomous neural hardening and adaptive routing management.</p>
                    </div>
                </div>
                <button onclick="toggleFeature('${name}')" class="w-11 h-6 ${enabled ? 'bg-sky-500' : 'bg-slate-800'} rounded-full flex items-center px-1 transition-all">
                    <div class="w-4 h-4 bg-white rounded-full shadow-md ${enabled ? 'ml-auto' : ''} transition-all"></div>
                </button>
            `;
            settingsContainer.appendChild(item);
        }
    });
}

// --- STARTUP ---

document.addEventListener('DOMContentLoaded', () => {
    switchTab('dashboard');
    fetchRegistry();
    fetchProxyStatus();
    fetchVersion();
    fetchServiceInfo();
    fetchFeatures();
    fetchNetworkInfo();
    connectToLogs();
    initCharts();
    document.getElementById('chat-input').addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendUserMessage(); }
    });
    setInterval(fetchRegistry, 30000);
});
