import { test, expect } from '@playwright/test';

test.describe('login overlay', () => {
    test.beforeEach(async ({ context, page }) => {
        // Ensure no stale auth in localStorage; the overlay should appear for fresh sessions.
        await context.clearCookies();
        await context.addInitScript(() => {
            try {
                window.localStorage.clear();
            } catch {
                /* no-op when storage is denied */
            }
        });
        // Stub identity/config to simulate API-key-required mode. The default
        // dev config ships with `server.auth.enabled: false` (fully open) —
        // in that mode auth.js correctly skips the overlay, which would
        // make these tests assert against the wrong scenario.
        await page.route('**/api/v1/identity/config', (route) =>
            route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({
                    enabled: false,
                    proxy_auth_enabled: true,
                    providers: [],
                }),
            }),
        );
        // /identity/me must reject anonymous + bogus keys for the "rejects
        // bad key" test to be meaningful against the same stub backend.
        await page.route('**/api/v1/identity/me', (route) =>
            route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({ authenticated: false }),
            }),
        );
    });

    test('shows the login overlay when no proxy_key is stored', async ({ page }) => {
        await page.goto('/ui/');
        // The overlay starts with `class="hidden"` and is unhidden by main.js once auth is checked.
        const overlay = page.locator('#login-overlay');
        await expect(overlay).toBeVisible({ timeout: 5_000 });
        await expect(overlay.getByRole('heading', { name: 'LLMProxy' })).toBeVisible();
        await expect(overlay.locator('#login-api-key')).toBeVisible();
    });

    test('rejects an obviously bad API key without breaking the page', async ({ page }) => {
        await page.goto('/ui/');
        const overlay = page.locator('#login-overlay');
        await expect(overlay).toBeVisible({ timeout: 5_000 });
        const input = overlay.locator('#login-api-key');
        await input.fill('not-a-real-key');
        await overlay.getByRole('button', { name: /enter/i }).click();
        // Either a visible error appears or the overlay stays — both are acceptable.
        // The contract: the page does not navigate away or crash.
        await page.waitForTimeout(500);
        await expect(overlay).toBeVisible();
    });
});
