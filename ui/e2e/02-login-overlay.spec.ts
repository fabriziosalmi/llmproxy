import { test, expect } from '@playwright/test';

test.describe('login overlay', () => {
    test.beforeEach(async ({ context }) => {
        // Ensure no stale auth in localStorage; the overlay should appear for fresh sessions.
        await context.clearCookies();
        await context.addInitScript(() => {
            try {
                window.localStorage.clear();
            } catch {
                /* no-op when storage is denied */
            }
        });
    });

    test('shows the login overlay when no proxy_key is stored', async ({ page }) => {
        await page.goto('/ui/');
        // The overlay starts with `class="hidden"` and is unhidden by main.js once auth is checked.
        const overlay = page.locator('#login-overlay');
        await expect(overlay).toBeVisible({ timeout: 5_000 });
        await expect(page.getByRole('heading', { name: 'LLMProxy' })).toBeVisible();
        await expect(page.getByLabel(/api key/i)).toBeVisible();
    });

    test('rejects an obviously bad API key without breaking the page', async ({ page }) => {
        await page.goto('/ui/');
        const input = page.getByLabel(/api key/i);
        await input.fill('not-a-real-key');
        await page.getByRole('button', { name: /enter/i }).click();
        // Either a visible error appears or the overlay stays — both are acceptable.
        // The contract: the page does not navigate away or crash.
        await page.waitForTimeout(500);
        await expect(page.locator('#login-overlay')).toBeVisible();
    });
});
