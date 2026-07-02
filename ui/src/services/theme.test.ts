import { describe, expect, it, beforeEach } from 'vitest';
import { getTheme, setTheme, initTheme, getThemePreference, setThemePreference, resolveTheme } from './theme';

/** Stub matchMedia so 'auto' resolution is deterministic in tests. */
function stubSystem(prefersLight: boolean): void {
    Object.defineProperty(globalThis, 'matchMedia', {
        configurable: true,
        value: (q: string) => ({
            matches: q.includes('light') ? prefersLight : !prefersLight,
            media: q,
            addEventListener: () => {},
            removeEventListener: () => {},
            addListener: () => {},
            removeListener: () => {},
        }),
    });
}

// happy-dom 20 ships a no-op localStorage shape — stub a real one so tests
// can exercise the persistence path. Only this file needs it; the rest of
// the suite either avoids storage or talks to it through a deps injection.
function installStorage(): void {
    const data: Record<string, string> = {};
    const store: Storage = {
        get length() {
            return Object.keys(data).length;
        },
        key: (i: number) => Object.keys(data)[i] ?? null,
        getItem: (k: string) => (k in data ? data[k]! : null),
        setItem: (k: string, v: string) => {
            data[k] = String(v);
        },
        removeItem: (k: string) => {
            delete data[k];
        },
        clear: () => {
            for (const k of Object.keys(data)) delete data[k];
        },
    };
    Object.defineProperty(globalThis, 'localStorage', { value: store, configurable: true });
}

describe('theme service', () => {
    beforeEach(() => {
        installStorage();
        document.documentElement.className = '';
        stubSystem(false); // deterministic: system prefers dark unless a test says otherwise
    });

    it('defaults to dark when no preference is stored', () => {
        expect(getTheme()).toBe('dark');
    });

    it('returns light when localStorage holds it', () => {
        localStorage.setItem('llmproxy.theme', 'light');
        expect(getTheme()).toBe('light');
    });

    it('any other stored value is treated as dark — defensive', () => {
        localStorage.setItem('llmproxy.theme', 'sepia');
        expect(getTheme()).toBe('dark');
    });

    it('setTheme("light") adds the .theme-light class and persists', () => {
        setTheme('light');
        expect(document.documentElement.classList.contains('theme-light')).toBe(true);
        expect(localStorage.getItem('llmproxy.theme')).toBe('light');
    });

    it('setTheme("dark") removes the .theme-light class and persists', () => {
        document.documentElement.classList.add('theme-light');
        setTheme('dark');
        expect(document.documentElement.classList.contains('theme-light')).toBe(false);
        expect(localStorage.getItem('llmproxy.theme')).toBe('dark');
    });

    it('initTheme applies the persisted theme so reloads do not flash', () => {
        localStorage.setItem('llmproxy.theme', 'light');
        const t = initTheme();
        expect(t).toBe('light');
        expect(document.documentElement.classList.contains('theme-light')).toBe(true);
    });

    it('initTheme returns dark and does NOT add the class when no preference is stored', () => {
        const t = initTheme();
        expect(t).toBe('dark');
        expect(document.documentElement.classList.contains('theme-light')).toBe(false);
    });

    // ── Appearance preference (auto/dark/light) ──
    it('defaults the preference to auto when nothing is stored', () => {
        expect(getThemePreference()).toBe('auto');
    });

    it('auto follows the client: resolves light when the system prefers light', () => {
        stubSystem(true);
        expect(getThemePreference()).toBe('auto');
        expect(resolveTheme()).toBe('light');
        expect(getTheme()).toBe('light');
    });

    it('auto follows the client: resolves dark when the system prefers dark', () => {
        stubSystem(false);
        expect(resolveTheme('auto')).toBe('dark');
    });

    it('an explicit preference overrides the system', () => {
        stubSystem(true); // system says light…
        setThemePreference('dark'); // …but the user pinned dark
        expect(getThemePreference()).toBe('dark');
        expect(getTheme()).toBe('dark');
        expect(document.documentElement.classList.contains('theme-light')).toBe(false);
    });

    it('setThemePreference("auto") applies the system theme and persists auto', () => {
        stubSystem(true);
        setThemePreference('auto');
        expect(localStorage.getItem('llmproxy.theme')).toBe('auto');
        expect(document.documentElement.classList.contains('theme-light')).toBe(true);
    });

    it('a legacy explicit "light" preference is still honoured', () => {
        localStorage.setItem('llmproxy.theme', 'light');
        expect(getThemePreference()).toBe('light');
        expect(getTheme()).toBe('light');
    });
});
