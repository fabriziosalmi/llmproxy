import { describe, expect, it, beforeEach } from 'vitest';
import { mountAppearance } from './Appearance';
import { getThemePreference } from '../../services/theme';

function installStorage(): void {
    const data: Record<string, string> = {};
    Object.defineProperty(globalThis, 'localStorage', {
        configurable: true,
        value: {
            getItem: (k: string) => (k in data ? data[k]! : null),
            setItem: (k: string, v: string) => {
                data[k] = String(v);
            },
            removeItem: (k: string) => delete data[k],
            clear: () => Object.keys(data).forEach((k) => delete data[k]),
            key: () => null,
            length: 0,
        },
    });
}

function stubSystem(prefersLight: boolean): void {
    Object.defineProperty(globalThis, 'matchMedia', {
        configurable: true,
        value: (q: string) => ({
            matches: q.includes('light') ? prefersLight : !prefersLight,
            media: q,
            addEventListener: () => {},
            removeEventListener: () => {},
        }),
    });
}

let host: HTMLElement;

beforeEach(() => {
    installStorage();
    stubSystem(false);
    document.documentElement.className = '';
    host = document.createElement('div');
    document.body.appendChild(host);
});

describe('mountAppearance', () => {
    it('renders the three appearance modes', () => {
        mountAppearance(host);
        expect(host.querySelector('[data-testid="appearance-auto"]')).not.toBeNull();
        expect(host.querySelector('[data-testid="appearance-dark"]')).not.toBeNull();
        expect(host.querySelector('[data-testid="appearance-light"]')).not.toBeNull();
    });

    it('pins light and applies the theme on click', () => {
        mountAppearance(host);
        host.querySelector<HTMLButtonElement>('[data-testid="appearance-light"]')!.click();
        expect(getThemePreference()).toBe('light');
        expect(document.documentElement.classList.contains('theme-light')).toBe(true);
    });

    it('returns to auto and drops the explicit class when system is dark', () => {
        mountAppearance(host);
        host.querySelector<HTMLButtonElement>('[data-testid="appearance-light"]')!.click();
        host.querySelector<HTMLButtonElement>('[data-testid="appearance-auto"]')!.click();
        expect(getThemePreference()).toBe('auto');
        // system stub prefers dark → auto resolves dark → no theme-light class
        expect(document.documentElement.classList.contains('theme-light')).toBe(false);
    });
});
