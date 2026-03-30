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
    }
};
