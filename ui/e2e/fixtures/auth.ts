import { test as base, expect, type Page } from '@playwright/test';

/**
 * The bearer token tests use to authenticate against the running proxy. Must
 * match one of the keys in `LLM_PROXY_API_KEYS` for the backend under test.
 * CI sets both sides via the workflow env; locally the default below works
 * with `.env.example`'s seeded key path through `install.sh`.
 */
export const TEST_PROXY_KEY = process.env.LLMPROXY_E2E_KEY ?? 'sk-proxy-ci-test-key';

/** Seed the proxy_key into localStorage before any page script runs. */
export async function seedAuth(page: Page, key: string = TEST_PROXY_KEY): Promise<void> {
    // addInitScript runs *before* the page's main script — guarantees the auth
    // token is in localStorage before main.js checks for it.
    await page.addInitScript((token) => {
        try {
            window.localStorage.setItem('proxy_key', token);
        } catch {
            /* private mode / quota — let the test surface the failure if it matters */
        }
    }, key);
}

interface AuthFixtures {
    /** A page that has already been pre-authenticated. Skips the login overlay. */
    authedPage: Page;
}

export const test = base.extend<AuthFixtures>({
    authedPage: async ({ page }, use) => {
        await seedAuth(page);
        await use(page);
    },
});

export { expect };
