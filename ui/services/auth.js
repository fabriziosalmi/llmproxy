/**
 * LLMPROXY — Auth Service (Session C)
 *
 * Handles OAuth/OIDC sign-in flow:
 *   1. Check if identity is enabled (GET /api/v1/identity/config)
 *   2. Google Sign-In via GIS library (popup)
 *   3. Generic OAuth popup for Microsoft/Apple
 *   4. Token exchange with backend (POST /api/v1/identity/exchange)
 *   5. Session management in localStorage
 */

const BASE_URL = window.location.origin;
const TOKEN_KEY = 'proxy_key';
const USER_KEY = 'proxy_user';

/**
 * Identity config response shape from /api/v1/identity/config.
 *   enabled            — SSO/OIDC mode is on
 *   proxy_auth_enabled — server.auth.enabled (API-key auth required)
 *   providers          — SSO providers (empty when SSO is off)
 *
 * Both flags can be independently true/false:
 *   - both false → fully open (no overlay)
 *   - proxy_auth_enabled only → API-key overlay
 *   - enabled only / both → SSO overlay (+ API-key fallback)
 *
 * @type {{ enabled: boolean, proxy_auth_enabled: boolean, providers: Array<{name:string, client_id:string, issuer:string}> } | null}
 */
let _identityConfig = null;

/** @type {{ email:string, name:string, roles:string[], provider:string } | null} */
let _currentUser = null;

// Well-known authorize endpoints for OAuth popup flow
const AUTHORIZE_URLS = {
    google: 'https://accounts.google.com/o/oauth2/v2/auth',
    microsoft: 'https://login.microsoftonline.com/common/oauth2/v2.0/authorize',
    apple: 'https://appleid.apple.com/auth/authorize',
};

// localStorage in Safari Private Mode and some sandboxed contexts throws on
// every access. Wrap so a SecurityError doesn't crash the boot path.
const _storage = {
    get(key) {
        try {
            return localStorage.getItem(key);
        } catch {
            return null;
        }
    },
    set(key, value) {
        try {
            localStorage.setItem(key, value);
            return true;
        } catch {
            return false;
        }
    },
    del(key) {
        try {
            localStorage.removeItem(key);
        } catch {
            /* noop */
        }
    },
};

// ─── Public API ───

export const auth = {
    /**
     * Initialize auth: fetch config, check existing session, show login if needed.
     * Returns true if user is authenticated or no auth is required.
     */
    async init() {
        try {
            const res = await fetch(`${BASE_URL}/api/v1/identity/config`);
            _identityConfig = res.ok ? _normalizeConfig(await res.json()) : _normalizeConfig({});
        } catch {
            _identityConfig = _normalizeConfig({});
        }

        // Fully-open mode: no SSO and no API-key auth required. The proxy
        // itself accepts unauthenticated requests, so gating the UI behind
        // an overlay would strand the user with no way through.
        if (!_identityConfig.enabled && !_identityConfig.proxy_auth_enabled) {
            _hideLoginOverlay();
            _updateUserUI();
            return true;
        }

        // Validate any existing token before trusting it. Both SSO and
        // API-key sessions probe /api/v1/identity/me, which is public-by-
        // design but does its own per-mode verification and returns
        // {authenticated: bool}. A revoked key sends the user back to the
        // overlay instead of looping 401s on every subsequent fetch.
        const token = _storage.get(TOKEN_KEY);
        if (token) {
            const result = await _validateToken(token);
            // Token stability: if logout() (or any other writer) replaced
            // the value while validation was in flight, the answer we got
            // describes a stale token. Bail without touching UI state so
            // the caller's intent wins.
            if (_storage.get(TOKEN_KEY) !== token) {
                _updateUserUI();
                return false;
            }
            if (result === 'valid') {
                _hideLoginOverlay();
                _updateUserUI();
                return true;
            }
            if (result === 'network') {
                // Transient network failure — keep the token (it may still
                // be valid) and surface the overlay so the user has a
                // visible path if the issue persists. Don't wipe state.
                _showLoginOverlay();
                if (_identityConfig.enabled) _renderProviderButtons();
                _showLoginError('Cannot reach proxy — check your connection.');
                _updateUserUI();
                return false;
            }
            // 'invalid' — drop stale credentials and fall through to overlay.
            _storage.del(TOKEN_KEY);
            _storage.del(USER_KEY);
            _currentUser = null;
        }

        _showLoginOverlay();
        if (_identityConfig.enabled) _renderProviderButtons();
        _updateUserUI();
        return false;
    },

    /** Get current user info (or null). */
    getUser() {
        if (_currentUser) return _currentUser;
        const raw = _storage.get(USER_KEY);
        if (raw) {
            try {
                _currentUser = JSON.parse(raw);
            } catch {
                _currentUser = null;
            }
        }
        return _currentUser;
    },

    /** Get current auth token (or empty string). */
    getToken() {
        return _storage.get(TOKEN_KEY) || '';
    },

    /** Is identity/SSO enabled? */
    isEnabled() {
        return _identityConfig?.enabled ?? false;
    },

    /**
     * Notify auth that an API-key login just succeeded (called by main.js
     * after it has validated the key and written it to localStorage).
     * Refreshes the user UI so logout becomes visible without a reload.
     */
    markApiKeyLoggedIn() {
        _hideLoginOverlay();
        _updateUserUI();
    },

    /** Log out: clear token + reload UI. */
    logout() {
        _storage.del(TOKEN_KEY);
        _storage.del(USER_KEY);
        _currentUser = null;
        _updateUserUI();
        // Only re-prompt when some form of auth is required. In fully-open
        // mode (both flags false) there is nothing to log into.
        if (_identityConfig?.enabled || _identityConfig?.proxy_auth_enabled) {
            _showLoginOverlay();
            if (_identityConfig?.enabled) _renderProviderButtons();
        }
    },
};

// ─── Session Validation ───

function _normalizeConfig(raw) {
    return {
        enabled: !!raw?.enabled,
        proxy_auth_enabled: !!raw?.proxy_auth_enabled,
        providers: Array.isArray(raw?.providers) ? raw.providers : [],
    };
}

/**
 * Probe /api/v1/identity/me. The endpoint is public-by-design and returns
 * {authenticated: bool}, gating on its own per-mode verification (SSO JWT
 * or API-key membership). Result:
 *   'valid'   — token works
 *   'invalid' — backend responded but rejected the token
 *   'network' — could not reach backend (don't wipe state)
 *
 * The SSO success branch also caches the identity payload.
 */
async function _validateToken(token) {
    let res;
    try {
        res = await fetch(`${BASE_URL}/api/v1/identity/me`, {
            headers: { Authorization: `Bearer ${token}` },
        });
    } catch {
        return 'network';
    }
    if (!res.ok) {
        // 5xx / unexpected — treat as transient. 401 from /identity/me is
        // not expected (the route is public), but if it ever happens, the
        // safe fallback is also 'network' rather than wiping a good token.
        return 'network';
    }
    let data;
    try {
        data = await res.json();
    } catch {
        return 'network';
    }
    if (!data?.authenticated) return 'invalid';
    if (data.provider && data.provider !== 'api_key') {
        _currentUser = {
            email: data.email,
            name: data.name,
            roles: data.roles || [],
            provider: data.provider,
        };
        _storage.set(USER_KEY, JSON.stringify(_currentUser));
    }
    return 'valid';
}

// ─── Token Exchange ───

async function _exchangeToken(externalToken) {
    try {
        const res = await fetch(`${BASE_URL}/api/v1/identity/exchange`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ token: externalToken }),
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || `HTTP ${res.status}`);
        }
        const data = await res.json();
        // Store proxy JWT
        _storage.set(TOKEN_KEY, data.token);
        _currentUser = data.identity;
        _storage.set(USER_KEY, JSON.stringify(_currentUser));
        _hideLoginOverlay();
        _updateUserUI();
        return true;
    } catch (e) {
        console.error('Token exchange failed:', e);
        _showLoginError(e.message);
        return false;
    }
}

// ─── OAuth Popup Flow ───

function _startOAuthPopup(provider) {
    const cfg = _identityConfig?.providers?.find((p) => p.name === provider.name);
    if (!cfg) return;

    const authorizeUrl = AUTHORIZE_URLS[provider.name];
    if (!authorizeUrl) {
        console.error(`No authorize URL for provider: ${provider.name}`);
        return;
    }

    const redirectUri = `${window.location.origin}/ui/oauth-callback.html`;
    const nonce = crypto.randomUUID?.() || Math.random().toString(36).slice(2);
    const state = crypto.randomUUID?.() || Math.random().toString(36).slice(2);

    const params = new URLSearchParams({
        client_id: cfg.client_id,
        redirect_uri: redirectUri,
        response_type: 'id_token',
        scope: 'openid email profile',
        nonce: nonce,
        state: state,
        prompt: 'select_account',
    });

    // Microsoft needs response_mode=fragment
    if (provider.name === 'microsoft') {
        params.set('response_mode', 'fragment');
    }

    const popup = window.open(
        `${authorizeUrl}?${params.toString()}`,
        'llmproxy_oauth',
        'width=500,height=650,scrollbars=yes'
    );

    // Listen for postMessage from callback page
    const handler = async (event) => {
        if (event.origin !== window.location.origin) return;
        if (event.data?.type !== 'oauth_callback') return;
        window.removeEventListener('message', handler);
        if (popup && !popup.closed) popup.close();

        if (event.data.id_token) {
            await _exchangeToken(event.data.id_token);
        } else if (event.data.error) {
            _showLoginError(event.data.error);
        }
    };
    window.addEventListener('message', handler);

    // Timeout: clean up if popup closed without response
    const checkClosed = setInterval(() => {
        if (popup && popup.closed) {
            clearInterval(checkClosed);
            window.removeEventListener('message', handler);
        }
    }, 1000);
}

// ─── UI Helpers ───

function _showLoginOverlay() {
    const overlay = document.getElementById('login-overlay');
    if (overlay) overlay.classList.remove('hidden');
}

function _hideLoginOverlay() {
    const overlay = document.getElementById('login-overlay');
    if (overlay) overlay.classList.add('hidden');
}

function _showLoginError(msg) {
    const el = document.getElementById('login-error');
    if (el) {
        el.textContent = msg;
        el.classList.remove('hidden');
        setTimeout(() => el.classList.add('hidden'), 5000);
    }
}

function _renderProviderButtons() {
    const container = document.getElementById('login-providers');
    if (!container || !_identityConfig?.providers) return;
    container.innerHTML = '';

    // SVG icons are static, controlled, no interpolation — safe to inline as HTML.
    const icons = {
        google: `<svg class="w-5 h-5" viewBox="0 0 24 24"><path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 01-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z"/><path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/><path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/><path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/></svg>`,
        microsoft: `<svg class="w-5 h-5" viewBox="0 0 24 24"><rect x="1" y="1" width="10" height="10" fill="#F25022"/><rect x="13" y="1" width="10" height="10" fill="#7FBA00"/><rect x="1" y="13" width="10" height="10" fill="#00A4EF"/><rect x="13" y="13" width="10" height="10" fill="#FFB900"/></svg>`,
        apple: `<svg class="w-5 h-5" fill="white" viewBox="0 0 24 24"><path d="M18.71 19.5c-.83 1.24-1.71 2.45-3.05 2.47-1.34.03-1.77-.79-3.29-.79-1.53 0-2 .77-3.27.82-1.31.05-2.3-1.32-3.14-2.53C4.25 17 2.94 12.45 4.7 9.39c.87-1.52 2.43-2.48 4.12-2.51 1.28-.02 2.5.87 3.29.87.78 0 2.26-1.07 3.8-.91.65.03 2.47.26 3.64 1.98-.09.06-2.17 1.28-2.15 3.81.03 3.02 2.65 4.03 2.68 4.04-.03.07-.42 1.44-1.38 2.83M13 3.5c.73-.83 1.94-1.46 2.94-1.5.13 1.17-.34 2.35-1.04 3.19-.69.85-1.83 1.51-2.95 1.42-.15-1.15.41-2.35 1.05-3.11z"/></svg>`,
    };

    const labels = {
        google: 'Sign in with Google',
        microsoft: 'Sign in with Microsoft',
        apple: 'Sign in with Apple',
    };

    const bgColors = {
        google: 'bg-white hover:bg-gray-100 text-gray-800',
        microsoft: 'bg-[#2F2F2F] hover:bg-[#3B3B3B] text-white',
        apple: 'bg-black hover:bg-gray-900 text-white border border-white/20',
    };

    for (const provider of _identityConfig.providers) {
        const btn = document.createElement('button');
        btn.className = `w-full flex items-center justify-center gap-3 px-4 py-3 rounded-xl text-sm font-semibold transition-all ${bgColors[provider.name] || 'bg-white/10 hover:bg-white/20 text-white'}`;
        // provider.name is operator-controlled (config-driven) but flows
        // through the SSO config endpoint — treat as untrusted and never
        // interpolate into innerHTML. Compose with safe DOM APIs instead.
        const iconHTML = icons[provider.name] || '';
        if (iconHTML) {
            const wrapper = document.createElement('span');
            wrapper.innerHTML = iconHTML;
            while (wrapper.firstChild) btn.appendChild(wrapper.firstChild);
        }
        const span = document.createElement('span');
        span.textContent = labels[provider.name] || `Sign in with ${provider.name}`;
        btn.appendChild(span);
        btn.addEventListener('click', () => _startOAuthPopup(provider));
        container.appendChild(btn);
    }
}

function _updateUserUI() {
    const user = auth.getUser();
    const nameEl = document.getElementById('user-display-name');
    const avatarEl = document.getElementById('user-avatar');
    const logoutBtn = document.getElementById('logout-btn');
    const loginHint = document.getElementById('login-hint');
    const hasToken = !!_storage.get(TOKEN_KEY);
    const ssoUser = !!(user && _identityConfig?.enabled);
    // Fully-open mode: hide all user chrome (no concept of "logged in").
    const requiresAuth = !!(_identityConfig?.enabled || _identityConfig?.proxy_auth_enabled);

    if (ssoUser) {
        if (nameEl) {
            nameEl.textContent = user.name || user.email || 'User';
            nameEl.classList.remove('hidden');
        }
        if (avatarEl) {
            const initials = (user.name || user.email || 'U').charAt(0).toUpperCase();
            avatarEl.textContent = initials;
            avatarEl.classList.remove('hidden');
        }
        if (logoutBtn) logoutBtn.classList.remove('hidden');
        if (loginHint) loginHint.classList.add('hidden');
    } else if (hasToken && requiresAuth) {
        // API-key auth has no identity payload — keep name/avatar hidden
        // but expose logout so the user can swap keys.
        if (nameEl) nameEl.classList.add('hidden');
        if (avatarEl) avatarEl.classList.add('hidden');
        if (logoutBtn) logoutBtn.classList.remove('hidden');
        if (loginHint) loginHint.classList.add('hidden');
    } else {
        if (nameEl) nameEl.classList.add('hidden');
        if (avatarEl) avatarEl.classList.add('hidden');
        if (logoutBtn) logoutBtn.classList.add('hidden');
        if (loginHint) {
            // No login hint in fully-open mode either.
            loginHint.classList.toggle('hidden', !requiresAuth);
        }
    }
}
