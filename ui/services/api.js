/**
 * LLMPROXY — Centralized API Service
 *
 * All requests go through _fetch() which auto-injects Authorization
 * headers and validates response status (fixes audit #21 + #22).
 */

const BASE_URL = window.location.origin;

/** Auth-aware fetch wrapper. Injects Bearer token and checks response status. */
async function _fetch(url, options = {}) {
    const token = localStorage.getItem('proxy_key') || '';
    const headers = { ...(options.headers || {}) };
    if (token) {
        headers['Authorization'] = `Bearer ${token}`;
    }
    const response = await fetch(url, { ...options, headers });
    if (!response.ok) {
        const body = await response.text().catch(() => '');
        const err = new Error(`API ${response.status}: ${body.slice(0, 200)}`);
        err.status = response.status;
        err.body = body;
        throw err;
    }
    return response;
}

async function _json(url, options) {
    const response = await _fetch(url, options);
    return await response.json();
}

async function _text(url, options) {
    const response = await _fetch(url, options);
    return await response.text();
}

function _post(url, data) {
    return _json(url, {
        method: 'POST',
        body: JSON.stringify(data),
        headers: { 'Content-Type': 'application/json' },
    });
}

export const api = {
    async fetchNetworkInfo() { return _json(`${BASE_URL}/api/v1/network/info`); },
    async fetchVersion() { return _json(`${BASE_URL}/api/v1/version`); },
    async fetchRegistry() { return _json(`${BASE_URL}/api/v1/registry`); },

    async toggleEndpoint(id) { return _fetch(`${BASE_URL}/api/v1/registry/${id}/toggle`, { method: 'POST' }); },
    async deleteEndpoint(id) { return _fetch(`${BASE_URL}/api/v1/registry/${id}`, { method: 'DELETE' }); },
    async updatePriority(id, priority) { return _post(`${BASE_URL}/api/v1/registry/${id}/priority`, { priority }); },

    async fetchProxyStatus() { return _json(`${BASE_URL}/api/v1/proxy/status`); },
    async toggleProxy(enabled) { return _post(`${BASE_URL}/api/v1/proxy/toggle`, { enabled }); },
    async togglePriorityMode(enabled) { return _post(`${BASE_URL}/api/v1/proxy/priority/toggle`, { enabled }); },

    async fetchServiceInfo() { return _json(`${BASE_URL}/api/v1/service-info`); },
    async fetchFeatures() { return _json(`${BASE_URL}/api/v1/features`); },
    async toggleFeature(name, enabled) { return _post(`${BASE_URL}/api/v1/features/toggle`, { name, enabled }); },

    async fetchPlugins() { return _json(`${BASE_URL}/api/v1/plugins`); },
    async togglePlugin(name, enabled) { return _post(`${BASE_URL}/api/v1/plugins/toggle`, { name, enabled }); },
    async panic() { return _json(`${BASE_URL}/api/v1/panic`, { method: 'POST' }); },
    async fetchPluginStats() { return _json(`${BASE_URL}/api/v1/plugins/stats`); },

    async fetchMetrics() { return _text(`${BASE_URL}/metrics`); },
    async fetchHealth() { return _json(`${BASE_URL}/health`); },
    async fetchGuardsStatus() { return _json(`${BASE_URL}/api/v1/guards/status`); },
    async fetchCacheStats() { return _json(`${BASE_URL}/api/v1/cache/stats`); },
    async fetchWebhooks() { return _json(`${BASE_URL}/api/v1/webhooks`); },
    async fetchExportStatus() { return _json(`${BASE_URL}/api/v1/export/status`); },
    async fetchRbacRoles() { return _json(`${BASE_URL}/api/v1/rbac/roles`); },
    async fetchIdentityMe() { return _json(`${BASE_URL}/api/v1/identity/me`); },

    async fetchLatencyMetrics() { return _json(`${BASE_URL}/api/v1/metrics/latency`); },
    async fetchRingTimeline() { return _json(`${BASE_URL}/api/v1/metrics/ring-timeline`); },

    async installPlugin(data) { return _post(`${BASE_URL}/api/v1/plugins/install`, data); },
    async uninstallPlugin(name) { return _json(`${BASE_URL}/api/v1/plugins/${name}`, { method: 'DELETE' }); },
    async rollbackPlugins() { return _json(`${BASE_URL}/api/v1/plugins/rollback`, { method: 'POST' }); },

    async sendChatMessage(text, model = 'auto') {
        return _json(`${BASE_URL}/v1/chat/completions`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ model, messages: [{ role: 'user', content: text }] }),
        });
    },

    async fetchModels() { return _json(`${BASE_URL}/v1/models`); },
    async fetchSpend(groupBy = 'model') { return _json(`${BASE_URL}/api/v1/analytics/spend?group_by=${groupBy}`); },
    async fetchTopModels(limit = 10) { return _json(`${BASE_URL}/api/v1/analytics/spend/topmodels?limit=${limit}`); },
    async fetchAudit(params = {}) {
        const qs = new URLSearchParams(params).toString();
        return _json(`${BASE_URL}/api/v1/audit?${qs}`);
    },
};
