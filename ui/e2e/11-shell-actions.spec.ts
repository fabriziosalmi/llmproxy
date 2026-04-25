import { test, expect } from './fixtures/auth';

/**
 * Shell-level operator actions that aren't tied to a single tab:
 *   1. Logout button clears the auth token and brings the login overlay back.
 *   2. Panic kill-switch goes through a confirm modal before POSTing /api/v1/panic;
 *      cancel must skip the request, confirm must transform the button into HALTED.
 *   3. Action failures (DELETE 500) surface a toast and leave the row in place
 *      — the optimistic optimistic-then-revert path is the most common error
 *      moment a user sees and we should pin it.
 *
 * These three flows are critical AND not previously covered.
 */

test.describe('shell actions', () => {
    test('logout clears proxy_key and re-shows the login overlay', async ({ authedPage }) => {
        // Stub identity so the login overlay isn't blocked on a real /identity/config call.
        await authedPage.route('**/api/v1/identity/config', async (route) => {
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({ enabled: true, providers: [] }),
            });
        });
        await authedPage.goto('/ui/');
        // Token is seeded by the fixture — overlay should NOT be visible at boot.
        await expect(authedPage.locator('#login-overlay')).not.toBeVisible({ timeout: 10_000 });

        // Sidebar logout button — main.js wires it to auth.logout().
        const logoutBtn = authedPage.locator('#logout-btn');
        await logoutBtn.click();

        // localStorage cleared.
        const token = await authedPage.evaluate(() => window.localStorage.getItem('proxy_key'));
        expect(token).toBeNull();

        // Overlay returns.
        await expect(authedPage.locator('#login-overlay')).toBeVisible({ timeout: 5_000 });
    });

    test('panic kill-switch confirms before POSTing /api/v1/panic', async ({ authedPage }) => {
        let panicCalled = false;
        await authedPage.route('**/api/v1/panic', async (route, request) => {
            if (request.method() === 'POST') panicCalled = true;
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({ status: 'HALTED' }),
            });
        });

        await authedPage.goto('/ui/');
        const panicBtn = authedPage.locator('#panic-btn');
        await expect(panicBtn).toBeVisible({ timeout: 10_000 });

        // 1st click → confirm modal mounts; cancel skips the POST.
        await panicBtn.click();
        const modal = authedPage.locator('[data-testid="modal-confirm"]');
        await expect(modal).toBeVisible();
        await authedPage.locator('[data-testid="modal-confirm-cancel"]').click();
        await expect(modal).not.toBeVisible();
        await authedPage.waitForTimeout(150);
        expect(panicCalled).toBe(false);

        // 2nd click → confirm fires the POST and transforms the button.
        await panicBtn.click();
        await expect(modal).toBeVisible();
        await authedPage.locator('[data-testid="modal-confirm-ok"]').click();
        await authedPage.waitForTimeout(300);
        expect(panicCalled).toBe(true);
        await expect(panicBtn).toContainText(/HALTED/i);
    });

    test('a 500 on delete-endpoint surfaces a toast and keeps the row', async ({ authedPage }) => {
        await authedPage.route('**/api/v1/registry', async (route, request) => {
            if (request.method() !== 'GET') {
                await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
                return;
            }
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify([
                    {
                        id: 'doomed',
                        url: 'https://api.openai.com/v1',
                        provider: 'openai',
                        status: 'Live',
                        circuit_state: 'closed',
                        priority: 1,
                    },
                ]),
            });
        });
        await authedPage.route('**/api/v1/registry/doomed', async (route, request) => {
            if (request.method() === 'DELETE') {
                await route.fulfill({ status: 500, contentType: 'application/json', body: '{"detail":"db locked"}' });
                return;
            }
            await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
        });

        await authedPage.goto('/ui/#/endpoints');
        await expect(authedPage.locator('[data-testid="ep-status-doomed"]')).toBeVisible({ timeout: 10_000 });

        await authedPage.locator('[data-testid="ep-delete-doomed"]').click();
        await authedPage.locator('[data-testid="modal-confirm-ok"]').click();

        // Toast container is the canonical surface for failure messages.
        const toastContainer = authedPage.locator('#toast-container');
        await expect(toastContainer).toContainText(/Delete failed|delete/i, { timeout: 3_000 });

        // Row should still be there — no optimistic removal on failure.
        await expect(authedPage.locator('[data-testid="ep-status-doomed"]')).toBeVisible();
    });
});
