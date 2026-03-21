/**
 * LLMPROXY — Entry Point (Final)
 */
import { store } from './services/store.js';
import { api } from './services/api.js';
import { renderSidebar, initSidebar } from './components/sidebar.js';
import { renderContent, initNavigation } from './components/content.js';
import { renderRegistry, fetchRegistry, initRegistry } from './components/registry.js';
import { initThreats, renderThreats } from './components/threats.js';
import { initGuards, renderGuards } from './components/guards.js';
import { renderSettings, initSettings } from './components/settings.js';
import { initLogs } from './components/logs.js';
import { initPlugins } from './components/plugins.js';
import { initModels } from './components/models.js';
import { initAnalytics } from './components/analytics.js';
import { auth } from './services/auth.js';

// Global state listener for UI updates
store.subscribe((state) => {
    renderSidebar();
    renderContent();
    renderRegistry();
    renderGuards();
    renderThreats();
    renderSettings();
});

// Helper function for navigation (assuming it's defined elsewhere or will be added)
function showSection(sectionId) {
    document.querySelectorAll('.content-view').forEach(view => {
        view.classList.add('hidden');
    });
    const targetView = document.getElementById(sectionId);
    if (targetView) {
        targetView.classList.remove('hidden');
    }
}

async function init() {
    // Session C: Initialize auth — check if SSO is required
    const authenticated = await auth.init();

    // Handle fallback from oauth-callback.html (direct navigation, no popup opener)
    const pendingToken = sessionStorage.getItem('_oauth_id_token');
    if (pendingToken) {
        sessionStorage.removeItem('_oauth_id_token');
        try {
            const res = await fetch(`${window.location.origin}/api/v1/identity/exchange`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ token: pendingToken }),
            });
            if (res.ok) {
                const data = await res.json();
                localStorage.setItem('proxy_key', data.token);
                localStorage.setItem('proxy_user', JSON.stringify(data.identity));
                window.location.reload();
                return;
            }
        } catch { /* ignore */ }
    }

    // Wire logout button
    const logoutBtn = document.getElementById('logout-btn');
    if (logoutBtn) {
        logoutBtn.addEventListener('click', () => auth.logout());
    }

    // Wire API key login button
    const apiKeyBtn = document.getElementById('login-api-key-btn');
    const apiKeyInput = document.getElementById('login-api-key');
    if (apiKeyBtn && apiKeyInput) {
        const doApiKeyLogin = () => {
            const key = apiKeyInput.value.trim();
            if (key) {
                localStorage.setItem('proxy_key', key);
                const overlay = document.getElementById('login-overlay');
                if (overlay) overlay.classList.add('hidden');
            }
        };
        apiKeyBtn.addEventListener('click', doApiKeyLogin);
        apiKeyInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') doApiKeyLogin(); });
    }

    // Show user section if authenticated
    if (auth.isEnabled() && auth.getUser()) {
        const section = document.getElementById('user-section');
        if (section) section.classList.remove('hidden');
        if (section) section.classList.add('flex');
    }

    // Initialize UI components first — must work even without backend
    const initWrappers = [
        { name: 'sidebar', fn: initSidebar },
        { name: 'navigation', fn: initNavigation },
        { name: 'threats', fn: initThreats },
        { name: 'guards', fn: initGuards },
        { name: 'registry', fn: initRegistry },
        { name: 'settings', fn: initSettings },
        { name: 'logs', fn: initLogs },
        { name: 'plugins', fn: initPlugins },
        { name: 'models', fn: initModels },
        { name: 'analytics', fn: initAnalytics },
    ];

    initWrappers.forEach(w => {
        try {
            w.fn();
        } catch (e) {
            console.warn(`UI Component [${w.name}] failed to initialize:`, e);
        }
    });

    // Init HUD global features (Cmd+K, Drawer, Cinema Mode)
    initHUD();

    // Fetch critical system state from backend
    try {
        const [status, features] = await Promise.all([
            api.fetchProxyStatus(),
            api.fetchFeatures(),
        ]);

        store.update({
            proxyEnabled: status.enabled,
            priorityMode: status.priority_mode || false,
            features: features
        });
    } catch (err) {
        console.warn("Backend unavailable — running in offline mode.", err);
    }

    // Background refresh
    setInterval(() => { try { fetchRegistry(); } catch(e) {} }, 30000);

    console.info("LLMPROXY Modular UI Environment: READY");
}

function initHUD() {
    // Cmd+K Palette
    const palette = document.getElementById('cmd-palette-overlay');
    const box = document.getElementById('cmd-palette-box');
    const input = document.getElementById('cmd-input');
    
    if (palette && box && input) {
        const togglePalette = () => {
            if (palette.classList.contains('hidden')) {
                palette.classList.remove('hidden');
                palette.classList.add('flex');
                setTimeout(() => {
                    box.classList.remove('scale-95', 'opacity-0');
                    box.classList.add('scale-100', 'opacity-100');
                    input.focus();
                }, 10);
            } else {
                box.classList.remove('scale-100', 'opacity-100');
                box.classList.add('scale-95', 'opacity-0');
                setTimeout(() => {
                    palette.classList.add('hidden');
                    palette.classList.remove('flex');
                }, 200);
            }
        };

        document.addEventListener('keydown', (e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
                e.preventDefault();
                togglePalette();
            }
            if (e.key === 'Escape' && !palette.classList.contains('hidden')) {
                togglePalette();
            }
        });
        
        palette.addEventListener('click', (e) => {
            if (e.target === palette) togglePalette();
        });

        // 11.7: WASM-accelerated (simulated) High-Performance Indexer
        const commands = [
            { id: 'view-threats', name: 'Nav: Threat Dashboard', desc: 'Security KPIs and event feed' },
            { id: 'view-guards', name: 'Nav: Security Guards', desc: 'Toggle injection/PII/link guards' },
            { id: 'view-plugins', name: 'Nav: Plugin Pipeline', desc: 'Ring-based security plugins' },
            { id: 'view-endpoints', name: 'Nav: Endpoints', desc: 'LLM endpoint registry' },
            { id: 'view-models', name: 'Nav: Models', desc: 'LLM model registry across all providers' },
            { id: 'view-analytics', name: 'Nav: Analytics', desc: 'Spend breakdown by model and provider' },
            { id: 'view-logs', name: 'Nav: Live Logs', desc: 'Real-time SSE log stream' },
            { id: 'view-settings', name: 'Nav: Settings', desc: 'Identity, rate limits, system info' },
            { id: 'toggle-proxy', name: 'System: Kill Switch', desc: 'Emergency halt all traffic' },
            { id: 'clear-logs', name: 'Terminal: Clear Buffer', desc: 'Clear audit log terminal' },
        ];

        const cmdList = document.getElementById('cmd-list');
        input.addEventListener('input', () => {
            const query = input.value.toLowerCase();
            if(!cmdList) return;
            cmdList.innerHTML = '';
            
            const results = commands.filter(c => 
                c.name.toLowerCase().includes(query) || 
                c.desc.toLowerCase().includes(query)
            );

            results.forEach(res => {
                const item = document.createElement('div');
                item.className = "flex items-center justify-between p-3 hover:bg-white/5 rounded-xl cursor-pointer transition-all border border-transparent hover:border-white/10 group";
                item.innerHTML = `
                    <div class="flex items-center gap-4">
                        <div class="p-2 bg-sky-500/10 rounded-lg text-sky-400 group-hover:scale-110 transition-transform">
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/></svg>
                        </div>
                        <div>
                            <p class="text-[11px] font-bold text-slate-100">${res.name}</p>
                            <p class="text-[9px] text-slate-500 font-medium">${res.desc}</p>
                        </div>
                    </div>
                    <div class="text-[9px] font-mono text-slate-700 bg-white/5 px-2 py-1 rounded">ENTER</div>
                `;
                item.onclick = () => {
                    executeCommand(res.id);
                    togglePalette();
                };
                cmdList.appendChild(item);
            });
        });
    }

    function executeCommand(id) {
        const targets = {
            'toggle-proxy': 'panic-btn',
            'clear-logs': 'clear-logs-btn',
            'view-threats': 'nav-threats',
            'view-guards': 'nav-guards',
            'view-plugins': 'nav-plugins',
            'view-endpoints': 'nav-endpoints',
            'view-models': 'nav-models',
            'view-analytics': 'nav-analytics',
            'view-logs': 'nav-logs',
            'view-settings': 'nav-settings',
        };
        const el = document.getElementById(targets[id]);
        if (el) el.click();
        console.info(`Executed HUD command: ${id}`);
    }

    // UX Feature 21: Cinema Mode (Focus UI)
    document.addEventListener('keydown', (e) => {
        if (e.key.toLowerCase() === 'f' && e.target.tagName !== 'INPUT' && e.target.tagName !== 'TEXTAREA') {
            document.body.classList.toggle('cinema-mode');
        }
    });

    // 15.15 Copy-to-id (One-Click)
    document.addEventListener('click', (e) => {
        const copyEl = e.target.closest('.copyable');
        if (copyEl) {
            const text = copyEl.innerText.replace('ID: ', '').trim();
            navigator.clipboard.writeText(text);
            const originalText = copyEl.innerText;
            copyEl.innerText = "COPIED!";
            copyEl.classList.add('text-emerald-400');
            setTimeout(() => {
                copyEl.innerText = originalText;
                copyEl.classList.remove('text-emerald-400');
            }, 1000);
        }
    });

    // Kill Switch Handler
    const panicBtn = document.getElementById('panic-btn');
    if (panicBtn) {
        panicBtn.addEventListener('click', async () => {
            if (confirm("EMERGENCY KILL SWITCH\n\nThis will immediately halt ALL proxy traffic.\nAre you sure?")) {
                try {
                    const data = await api.panic();
                    if (data.status === 'HALTED') {
                        store.update({ proxyEnabled: false });
                        panicBtn.innerHTML = `
                            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M18.364 18.364A9 9 0 005.636 5.636m12.728 12.728A9 9 0 015.636 5.636m12.728 12.728L5.636 5.636"/></svg>
                            HALTED`;
                        panicBtn.classList.add('bg-rose-500/30', 'border-rose-500/50');
                        document.getElementById('nav-guards')?.click();
                    }
                } catch (e) {
                    console.error('Kill switch failed:', e);
                }
            }
        });
    }

    // 15.18 Network Status Heartbeat
    setInterval(async () => {
        const statusDot = document.getElementById('status-dot');
        const statusText = document.getElementById('status-text');
        if (!statusDot || !statusText) return;
        try {
            const res = await fetch('/api/v1/proxy/status');
            if (!res.ok) throw new Error();
            statusDot.className = "w-1.5 h-1.5 rounded-full bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.4)] animate-pulse";
            statusText.innerText = "Live";
            statusText.className = "text-[9px] font-black text-emerald-400 uppercase tracking-widest";
        } catch (e) {
            statusDot.className = "w-1.5 h-1.5 rounded-full bg-rose-500 shadow-[0_0_8px_rgba(244,63,94,0.4)]";
            statusText.innerText = "Offline";
            statusText.className = "text-[9px] font-black text-rose-500 uppercase tracking-widest";
        }
    }, 5000);

    // UX Feature 24: Audio/Haptics Physics
    let audioCtx = null;
    function playBloop() {
        if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        if(audioCtx.state === 'suspended') audioCtx.resume();
        const osc = audioCtx.createOscillator();
        const gain = audioCtx.createGain();
        osc.type = 'sine';
        osc.frequency.setValueAtTime(600, audioCtx.currentTime);
        osc.frequency.exponentialRampToValueAtTime(300, audioCtx.currentTime + 0.1);
        gain.gain.setValueAtTime(0.05, audioCtx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + 0.1);
        osc.connect(gain);
        gain.connect(audioCtx.destination);
        osc.start();
        osc.stop(audioCtx.currentTime + 0.1);
    }
    
    document.addEventListener('click', (e) => {
        if (e.target.closest('button, a, .content-view label, .priority-input, tr')) {
            try { playBloop(); } catch(err) {} // Fail silently if audio context restricted
        }
    });
}

document.addEventListener('DOMContentLoaded', init);
