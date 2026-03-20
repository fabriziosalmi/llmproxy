/**
 * LLMPROXY — Entry Point (Final)
 */
import { store } from './services/store.js';
import { api } from './services/api.js';
import { renderSidebar, initSidebar } from './components/sidebar.js';
import { renderContent, initNavigation } from './components/content.js';
import { renderRegistry, fetchRegistry, initRegistry } from './components/registry.js';
import { renderProxy, initProxy } from './components/proxy.js';
import { renderDashboard, initDashboard } from './components/dashboard.js';
import { initChat } from './components/chat.js';
import { renderSettings } from './components/settings.js';
import { initLogs } from './components/logs.js';
import { initPlugins } from './components/plugins.js'; // NEW: Import initPlugins

// Global state listener for UI updates
store.subscribe((state) => {
    renderSidebar();
    renderContent();
    renderRegistry();
    renderProxy();
    renderDashboard();
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
    // Initial fetch of critical system state
    try {
        const [status, features, network, version] = await Promise.all([
            api.fetchProxyStatus(),
            api.fetchFeatures(),
            api.fetchNetworkInfo(),
            api.fetchVersion()
        ]);

        store.update({ 
            proxyEnabled: status.enabled,
            priorityMode: status.priority_mode || false,
            features: features
        });

        // Component initialization
        initDashboard(); // NEW: Initialize Dashboard
        initRegistry(); // NEW: Initialize Registry
        initChat();
        initLogs();
        initPlugins(); // NEW: Initialize Plugins

        // Navigation setup
        const sections = ['dashboard-view', 'registry-view', 'chat-view', 'proxy-view', 'plugins-view'];
        const navItems = ['nav-dashboard', 'nav-registry', 'nav-chat', 'nav-proxy', 'nav-plugins'];

        navItems.forEach((id, index) => {
            const el = document.getElementById(id);
            if (el) {
                el.addEventListener('click', (e) => {
                    e.preventDefault();
                    showSection(sections[index]);
                });
            }
        });

        // Initialize UI components and listeners with individual protection
        const initWrappers = [
            { name: 'sidebar', fn: initSidebar },
            { name: 'navigation', fn: initNavigation },
            { name: 'proxy', fn: initProxy },
            { name: 'chat', fn: initChat },
            { name: 'logs', fn: initLogs }
        ];

        initWrappers.forEach(w => {
            try {
                w.fn();
            } catch (e) {
                console.warn(`UI Component [${w.name}] failed to initialize:`, e);
            }
        });
        
        // Initial data load for tables
        fetchRegistry();
        
        // Init HUD global features (Cmd+K, Drawer)
        initHUD();
        
        // Set intervals for background refresh
        setInterval(fetchRegistry, 30000);
        
        console.info("LLMPROXY Modular UI Environment: READY");
    } catch (err) {
    }
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
            { id: 'toggle-proxy', name: 'System: Toggle Proxy Gate', desc: 'Kill/Start all neural traffic' },
            { id: 'clear-logs', name: 'Terminal: Flush Buffer', desc: 'Clear xterm.js WebGL cache' },
            { id: 'view-registry', name: 'Nav: Service Registry', desc: 'Inspect live endpoints' },
            { id: 'view-chat', name: 'Nav: Neural Chat', desc: 'Direct interaction mode' },
            { id: 'view-plugins', name: 'Nav: Plugin Hub', desc: 'Manage Neural OS modules' },
            { id: 'zenith-hard-reset', name: 'Zenith: Hard Reset', desc: 'Clear all memory and restart' }
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
        if (id === 'toggle-proxy') document.getElementById('nav-proxy').click();
        if (id === 'clear-logs') document.getElementById('clear-logs-btn').click();
        if (id === 'view-registry') document.getElementById('nav-registry').click();
        if (id === 'view-chat') document.getElementById('nav-chat').click();
        if (id === 'view-plugins') document.getElementById('nav-plugins').click();
        console.info(`Executed HUD command: ${id}`);
    }

    // Telemetry Slide-over Drawer
    const drawer = document.getElementById('telemetry-drawer');
    const btn = document.getElementById('btn-telemetry');
    const closeBtn = document.getElementById('close-telemetry-btn');
    
    if (drawer && btn && closeBtn) {
        const toggleDrawer = () => {
            if (drawer.classList.contains('translate-x-full')) {
                drawer.classList.remove('translate-x-full');
                drawer.classList.add('translate-x-0');
            } else {
                drawer.classList.remove('translate-x-0');
                drawer.classList.add('translate-x-full');
            }
        };

        btn.addEventListener('click', toggleDrawer);
        closeBtn.addEventListener('click', toggleDrawer);
    }

    // UX Feature 21: Cinema Mode (Focus UI)
    document.addEventListener('keydown', (e) => {
        if (e.key.toLowerCase() === 'f' && e.target.tagName !== 'INPUT' && e.target.tagName !== 'TEXTAREA') {
            document.body.classList.toggle('cinema-mode');
        }
    });

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
