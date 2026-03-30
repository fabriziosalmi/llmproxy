/**
 * LLMProxy Security Gateway — Global State
 */

export const store = {
    state: {
        isCollapsed: false,
        currentTab: 'threats',
        registry: [],
        proxyEnabled: true,
        priorityMode: false,
        features: {},
        logSource: null,
        // Security metrics
        securityStats: {
            requests: 0,
            blocked: 0,
            piiMasked: 0,
            passRate: '100%',
        },
    },

    listeners: [],

    subscribe(fn) {
        this.listeners.push(fn);
    },

    notify() {
        this.listeners.forEach(fn => fn(this.state));
    },

    update(patch) {
        this.state = { ...this.state, ...patch };
        this.notify();
    },

    /**
     * Create a polling interval that auto-pauses when:
     * - The page is hidden (Page Visibility API)
     * - The active tab doesn't match requiredTab (if specified)
     *
     * Returns a cleanup function. Fixes audit #13 — view-scoped polling.
     */
    poll(fn, intervalMs, requiredTab = null) {
        let timer = null;

        const tick = () => {
            if (document.hidden) return;
            if (requiredTab && this.state.currentTab !== requiredTab) return;
            try { fn(); } catch {}
        };

        const start = () => { if (!timer) timer = setInterval(tick, intervalMs); };
        const stop = () => { if (timer) { clearInterval(timer); timer = null; } };

        // Pause when page hidden
        document.addEventListener('visibilitychange', () => {
            document.hidden ? stop() : start();
        });

        start();
        return stop;
    },
};
