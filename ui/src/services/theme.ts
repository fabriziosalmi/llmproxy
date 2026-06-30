/**
 * Theme service — decoupled appearance, driven by the client.
 *
 * A *preference* (`auto` | `dark` | `light`) is persisted; the *resolved* theme
 * (`dark` | `light`) is what actually paints. `auto` (the default) follows the
 * client's `prefers-color-scheme` and reacts live to OS changes — so the UI
 * styles itself to the client automatically, with no backend involvement. The
 * Settings → Appearance control and the header toggle override the preference.
 *
 * Active theme is tracked on `<html>.theme-light` (presence = light); CSS
 * overrides in tokens.css / style.css key off it. Persisted to
 * `localStorage["llmproxy.theme"]`.
 */

const STORAGE_KEY = 'llmproxy.theme';

export type Theme = 'dark' | 'light';
export type ThemePreference = 'auto' | 'dark' | 'light';

function safeStorage(): Storage | null {
    try {
        return typeof localStorage !== 'undefined' ? localStorage : null;
    } catch {
        return null;
    }
}

/** The client's OS-level preference. Defaults to dark when unknown. */
function systemTheme(): Theme {
    try {
        return typeof matchMedia !== 'undefined' && matchMedia('(prefers-color-scheme: light)').matches
            ? 'light'
            : 'dark';
    } catch {
        return 'dark';
    }
}

/** Stored preference. Missing/invalid → 'auto' (follow the client). */
export function getThemePreference(): ThemePreference {
    const raw = safeStorage()?.getItem(STORAGE_KEY);
    return raw === 'light' || raw === 'dark' || raw === 'auto' ? raw : 'auto';
}

/** Resolve a preference to the concrete theme that paints. */
export function resolveTheme(pref: ThemePreference = getThemePreference()): Theme {
    return pref === 'auto' ? systemTheme() : pref;
}

/** The resolved active theme (back-compat with the binary API). */
export function getTheme(): Theme {
    return resolveTheme();
}

const _listeners = new Set<(t: Theme) => void>();

/** Subscribe to resolved-theme changes (preference change or live OS change). */
export function onThemeChange(fn: (t: Theme) => void): () => void {
    _listeners.add(fn);
    return () => _listeners.delete(fn);
}

function applyResolved(): Theme {
    const theme = resolveTheme();
    const root = typeof document !== 'undefined' ? document.documentElement : null;
    if (root) root.classList.toggle('theme-light', theme === 'light');
    for (const fn of _listeners) {
        try {
            fn(theme);
        } catch {
            /* a bad subscriber must not break theming */
        }
    }
    return theme;
}

/** Set the appearance preference (auto/dark/light), persist, and apply. */
export function setThemePreference(pref: ThemePreference): Theme {
    safeStorage()?.setItem(STORAGE_KEY, pref);
    return applyResolved();
}

/** Back-compat: set an explicit theme (header toggle). */
export function setTheme(next: Theme): void {
    setThemePreference(next);
}

let _mediaBound = false;

/** Apply the persisted preference on boot and bind the live OS listener. Idempotent. */
export function initTheme(): Theme {
    if (!_mediaBound && typeof matchMedia !== 'undefined') {
        try {
            const mq = matchMedia('(prefers-color-scheme: light)');
            const onSystemChange = () => {
                // Only auto-follows when the user hasn't pinned an explicit theme.
                if (getThemePreference() === 'auto') applyResolved();
            };
            mq.addEventListener?.('change', onSystemChange);
            _mediaBound = true;
        } catch {
            /* matchMedia unsupported — preference still works, just no live follow */
        }
    }
    return applyResolved();
}
