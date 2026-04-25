import { test, expect } from '@playwright/test';

test.describe('app boot', () => {
    test('serves the UI shell with the expected title', async ({ page }) => {
        const response = await page.goto('/ui/');
        expect(response?.status()).toBeLessThan(400);
        await expect(page).toHaveTitle(/LLMProxy/i);
    });

    test('exposes a healthy backend', async ({ request }) => {
        const r = await request.get('/health');
        expect(r.ok()).toBeTruthy();
        const body = await r.json();
        // Health shape varies by deployment but at minimum it must be JSON.
        expect(typeof body).toBe('object');
    });

    test('static assets load without 404 in the network log', async ({ page }) => {
        const failed: string[] = [];
        page.on('response', (resp) => {
            const url = resp.url();
            // Only track our own static assets, not upstream API calls or analytics beacons
            if (resp.status() >= 400 && (url.includes('/ui/') || url.endsWith('.css') || url.endsWith('.js'))) {
                failed.push(`${resp.status()} ${url}`);
            }
        });
        await page.goto('/ui/');
        await page.waitForLoadState('networkidle');
        expect(failed, `Failed asset requests:\n${failed.join('\n')}`).toEqual([]);
    });
});
