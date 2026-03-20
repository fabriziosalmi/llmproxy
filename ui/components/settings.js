/**
 * Settings View — Identity, system info, rate limiting config.
 */
import { api } from '../services/api.js';

export async function initSettings() {
    try {
        const [version, info] = await Promise.all([
            api.fetchVersion(),
            api.fetchServiceInfo(),
        ]);
        const verEl = document.getElementById('sys-version');
        const urlEl = document.getElementById('sys-url');
        if (verEl) verEl.textContent = version.version || '--';
        if (urlEl) urlEl.textContent = info.url || '--';
    } catch {}

    try {
        const res = await fetch(`${window.location.origin}/api/v1/identity/config`);
        const data = await res.json();
        const modeEl = document.getElementById('auth-mode');
        const ssoEl = document.getElementById('sso-status');
        if (modeEl) modeEl.textContent = data.enabled ? 'SSO / OIDC' : 'API Key';
        if (ssoEl) ssoEl.textContent = data.enabled ? 'Enabled' : 'Disabled';
    } catch {}
}

export function renderSettings() {
    // Static content — no reactive rendering needed
}
