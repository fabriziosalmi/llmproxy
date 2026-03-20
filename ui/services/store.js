/**
 * LLMPROXY — Global State Management
 */

export const store = {
    state: {
        isCollapsed: false,
        currentTab: 'dashboard',
        registry: [],
        proxyEnabled: true,
        priorityMode: false,
        features: {},
        logSource: null
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
