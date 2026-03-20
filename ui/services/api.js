/**
 * LLMPROXY — Centralized API Service
 */

const BASE_URL = window.location.origin;

export const api = {
    async fetchNetworkInfo() {
        const response = await fetch(`${BASE_URL}/api/v1/network/info`);
        return await response.json();
    },

    async fetchVersion() {
        const response = await fetch(`${BASE_URL}/api/v1/version`);
        return await response.json();
    },

    async fetchRegistry() {
        const response = await fetch(`${BASE_URL}/api/v1/registry`);
        return await response.json();
    },

    async toggleEndpoint(id) {
        return await fetch(`${BASE_URL}/api/v1/registry/${id}/toggle`, { method: 'POST' });
    },

    async deleteEndpoint(id) {
        return await fetch(`${BASE_URL}/api/v1/registry/${id}`, { method: 'DELETE' });
    },

    async updatePriority(id, priority) {
        return await fetch(`${BASE_URL}/api/v1/registry/${id}/priority`, {
            method: 'POST',
            body: JSON.stringify({ priority }),
            headers: { 'Content-Type': 'application/json' }
        });
    },

    async fetchProxyStatus() {
        const response = await fetch(`${BASE_URL}/api/v1/proxy/status`);
        return await response.json();
    },

    async toggleProxy(enabled) {
        const response = await fetch(`${BASE_URL}/api/v1/proxy/toggle`, {
            method: 'POST',
            body: JSON.stringify({ enabled }),
            headers: { 'Content-Type': 'application/json' }
        });
        return await response.json();
    },

    async togglePriorityMode(enabled) {
        const response = await fetch(`${BASE_URL}/api/v1/proxy/priority/toggle`, {
            method: 'POST',
            body: JSON.stringify({ enabled }),
            headers: { 'Content-Type': 'application/json' }
        });
        return await response.json();
    },

    async fetchServiceInfo() {
        const response = await fetch(`${BASE_URL}/api/v1/service-info`);
        return await response.json();
    },

    async fetchFeatures() {
        const response = await fetch(`${BASE_URL}/api/v1/features`);
        return await response.json();
    },

    async toggleFeature(name, enabled) {
        const response = await fetch(`${BASE_URL}/api/v1/features/toggle`, {
            method: 'POST',
            body: JSON.stringify({ name, enabled }),
            headers: { 'Content-Type': 'application/json' }
        });
        return await response.json();
    },

    async fetchPlugins() {
        const response = await fetch(`${BASE_URL}/api/v1/plugins`);
        return await response.json();
    },

    async togglePlugin(name, enabled) {
        return await fetch(`${BASE_URL}/api/v1/plugins/toggle`, {
            method: 'POST',
            body: JSON.stringify({ name, enabled }),
            headers: { 'Content-Type': 'application/json' }
        });
    },

    async panic() {
        const response = await fetch(`${BASE_URL}/api/v1/panic`, { method: 'POST' });
        return await response.json();
    },

    async fetchPluginStats() {
        const response = await fetch(`${BASE_URL}/api/v1/plugins/stats`);
        return await response.json();
    },

    async fetchMetrics() {
        const response = await fetch(`${BASE_URL}/metrics`);
        return await response.text();
    },

    async fetchHealth() {
        const response = await fetch(`${BASE_URL}/health`);
        return await response.json();
    },

    async fetchGuardsStatus() {
        const response = await fetch(`${BASE_URL}/api/v1/guards/status`);
        return await response.json();
    },

    async fetchWebhooks() {
        const response = await fetch(`${BASE_URL}/api/v1/webhooks`);
        return await response.json();
    },

    async fetchExportStatus() {
        const response = await fetch(`${BASE_URL}/api/v1/export/status`);
        return await response.json();
    },

    async fetchRbacRoles() {
        const response = await fetch(`${BASE_URL}/api/v1/rbac/roles`);
        return await response.json();
    },

    async fetchIdentityMe() {
        const response = await fetch(`${BASE_URL}/api/v1/identity/me`, {
            headers: { 'Authorization': `Bearer ${localStorage.getItem('proxy_key') || ''}` }
        });
        return await response.json();
    },

    async sendChatMessage(text, model = 'auto') {
        return await fetch(`${BASE_URL}/v1/chat/completions`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${localStorage.getItem('proxy_key') || ''}`
            },
            body: JSON.stringify({
                model,
                messages: [{ role: 'user', content: text }]
            })
        });
    }
};
