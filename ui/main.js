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
        
        // Set intervals for background refresh
        setInterval(fetchRegistry, 30000);
        
        console.info("LLMPROXY Modular UI Environment: READY");
    } catch (err) {
        console.error("Critical UI Initialization Failure:", err);
    }
}

document.addEventListener('DOMContentLoaded', init);
