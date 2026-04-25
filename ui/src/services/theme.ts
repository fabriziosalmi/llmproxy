/**
 * Theme service — applies and persists the `dark` / `light` theme.
 *
 * The dark theme is the default; light is opt-in. The toggle lives in
 * Settings. We track the active theme on `<html>.theme-light` (presence
 * = light, absence = dark) and persist to `localStorage.theme`.
 *
 * No subscribers needed today: the toggle reads/writes through the
 * exposed setTheme() and the CSS overrides in tokens.css apply
 * automatically on the next paint.
 */

const STORAGE_KEY = 'llmproxy.theme';

export type Theme = 'dark' | 'light';

function safeStorage(): Storage | null {
    try {
        return typeof localStorage !== 'undefined' ? localStorage : null;
    } catch {
        return null;
    }
}

export function getTheme(): Theme {
    const raw = safeStorage()?.getItem(STORAGE_KEY);
    return raw === 'light' ? 'light' : 'dark';
}

export function setTheme(next: Theme): void {
    const root = typeof document !== 'undefined' ? document.documentElement : null;
    if (root) {
        root.classList.toggle('theme-light', next === 'light');
    }
    safeStorage()?.setItem(STORAGE_KEY, next);
}

/** Apply the persisted theme on boot. Idempotent. */
export function initTheme(): Theme {
    const t = getTheme();
    setTheme(t);
    return t;
}
