/**
 * LLMPROXY — Entry Point (Final)
 */
import { store } from './services/store.js';
import { api } from './services/api.js';
import { renderSidebar, initSidebar } from './components/sidebar.js';
import { renderContent, initNavigation } from './components/content.js';
import { renderRegistry, fetchRegistry } from './components/registry.js';
import { renderProxy, initProxy } from './components/proxy.js';
import { renderDashboard } from './components/dashboard.js';
import { initChat } from './components/chat.js';
import { renderSettings } from './components/settings.js';
import { initLogs } from './components/logs.js';

// Global state listener for UI updates
store.subscribe((state) => {
    renderSidebar();
    renderContent();
    renderRegistry();
    renderProxy();
    renderDashboard();
    renderSettings();
});

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
