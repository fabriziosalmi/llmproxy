/**
 * Global time range selector.
 *
 * Single source of truth for the active time window — preset (1h / 4h / 24h
 * / 7d) or custom [from, to]. Persisted in the URL hash so a shared link
 * lands the recipient in the same context, and in localStorage so reloads
 * survive it. Views that filter by time subscribe to changes and refetch.
 *
 * Contract:
 *   timerange.get()           → { preset, from, to, windowMs }
 *   timerange.set(partial)    → merges + broadcasts
 *   timerange.subscribe(fn)   → returns unsubscriber
 *   timerange.sinceEpochMs()  → lower bound for queries, null for "all time"
 *
 * Data model:
 *   preset ∈ {'1h','4h','24h','7d','custom','all'}
 *   from, to: epoch ms. Only populated when preset='custom'.
 */

const STORAGE_KEY = 'llmproxy.timerange';
const DEFAULT = { preset: '24h', from: null, to: null };

const PRESET_WINDOW_MS = {
    '1h': 1 * 3600 * 1000,
    '4h': 4 * 3600 * 1000,
    '24h': 24 * 3600 * 1000,
    '7d': 7 * 24 * 3600 * 1000,
    all: null,
};

function _loadInitial() {
    // URL hash wins over localStorage so shareable links actually take effect.
    const hash = window.location.hash || '';
    const m = hash.match(/[?&]tr=([^&]+)/);
    if (m) {
        const raw = decodeURIComponent(m[1]);
        if (raw in PRESET_WINDOW_MS) return { preset: raw, from: null, to: null };
        // custom: tr=custom:<fromEpochMs>:<toEpochMs>
        const [tag, from, to] = raw.split(':');
        if (tag === 'custom' && from && to) {
            return { preset: 'custom', from: Number(from), to: Number(to) };
        }
    }
    try {
        const stored = localStorage.getItem(STORAGE_KEY);
        if (stored) {
            const parsed = JSON.parse(stored);
            if (parsed && parsed.preset) return parsed;
        }
    } catch {
        /* ignore */
    }
    return { ...DEFAULT };
}

let _state = _loadInitial();
const _listeners = new Set();

function _persistHash() {
    // Keep the tr=… param in the hash without clobbering the view hash (#/audit).
    const hash = window.location.hash || '';
    const view = hash.split('?')[0] || '#/threats';
    let tag;
    if (_state.preset === 'custom') tag = `custom:${_state.from}:${_state.to}`;
    else tag = _state.preset;
    const next = `${view}?tr=${encodeURIComponent(tag)}`;
    try {
        history.replaceState(null, '', next);
    } catch {
        /* ignore */
    }
}

function _persistStorage() {
    try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(_state));
    } catch {
        /* quota, etc. — non-critical */
    }
}

function _broadcast() {
    for (const fn of _listeners) {
        try {
            fn(_state);
        } catch (e) {
            console.warn('timerange listener failed:', e);
        }
    }
}

export const timerange = {
    get() {
        return { ..._state };
    },

    set(partial) {
        _state = { ..._state, ...partial };
        if (_state.preset && _state.preset !== 'custom') {
            _state.from = null;
            _state.to = null;
        }
        _persistHash();
        _persistStorage();
        _broadcast();
    },

    subscribe(fn) {
        _listeners.add(fn);
        return () => _listeners.delete(fn);
    },

    /**
     * Epoch ms for the lower bound of the current window, or null if "all time".
     * Views can pass this as `since`/`after` to queries; the backend filter is
     * still optional — the UI can also post-filter if an endpoint doesn't
     * accept a time param.
     */
    sinceEpochMs() {
        if (_state.preset === 'all') return null;
        if (_state.preset === 'custom') return _state.from || null;
        const win = PRESET_WINDOW_MS[_state.preset];
        if (!win) return null;
        return Date.now() - win;
    },

    untilEpochMs() {
        if (_state.preset === 'custom') return _state.to || null;
        return null; // "now"
    },

    /** Human label for the current selection — used by the context bar. */
    label() {
        const m = {
            '1h': 'Last hour',
            '4h': 'Last 4 hours',
            '24h': 'Last 24 hours',
            '7d': 'Last 7 days',
            all: 'All time',
            custom: 'Custom',
        };
        return m[_state.preset] || _state.preset;
    },
};

const PRESETS_ORDER = ['1h', '4h', '24h', '7d', 'all'];

/**
 * Mount the time-range selector in the page header. Idempotent.
 */
export function initTimerange() {
    const anchor = document.getElementById('timerange-slot');
    if (!anchor) return;
    if (anchor.querySelector('[data-tr-root]')) return;

    const wrap = document.createElement('div');
    wrap.setAttribute('data-tr-root', '');
    wrap.className = 'flex items-center gap-1 text-[10px] font-mono';

    const label = document.createElement('span');
    label.className = 'text-slate-500 mr-1 hidden md:inline';
    label.textContent = 'Range:';
    wrap.appendChild(label);

    const btns = {};
    for (const p of PRESETS_ORDER) {
        const b = document.createElement('button');
        b.type = 'button';
        b.textContent = p;
        b.className = 'px-2 py-1 rounded text-slate-500 hover:text-white hover:bg-white/5 transition-colors';
        b.setAttribute('aria-pressed', 'false');
        b.addEventListener('click', () => timerange.set({ preset: p }));
        wrap.appendChild(b);
        btns[p] = b;
    }

    const refresh = (state) => {
        for (const p of PRESETS_ORDER) {
            const active = state.preset === p;
            btns[p].className = active
                ? 'px-2 py-1 rounded text-cyan-300 bg-cyan-500/10 border border-cyan-500/30 transition-colors'
                : 'px-2 py-1 rounded text-slate-500 hover:text-white hover:bg-white/5 border border-transparent transition-colors';
            btns[p].setAttribute('aria-pressed', active ? 'true' : 'false');
        }
    };
    refresh(_state);
    timerange.subscribe(refresh);

    anchor.appendChild(wrap);
}
