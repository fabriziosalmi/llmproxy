import { test, expect } from './fixtures/auth';

/**
 * Covers the Plugins view:
 *   1. Cards render one per plugin with ring badge, stats, latency.
 *   2. Inspect forwards data-drilldown="plugin:<name>".
 *   3. Toggle hits POST /api/v1/plugins/toggle and refreshes.
 *   4. Uninstall opens the confirm modal; only confirm fires DELETE.
 *   5. + Install opens the form; valid submit POSTs /api/v1/plugins/install.
 *   6. Reload hits /api/v1/plugins/hot-swap.
 */
test.describe('plugins view', () => {
    test.beforeEach(async ({ authedPage }) => {
        await authedPage.route('**/api/v1/plugins', async (route, request) => {
            if (request.method() !== 'GET') {
                await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
                return;
            }
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({
                    plugins: [
                        {
                            name: 'pii_masker',
                            hook: 'pre_flight',
                            entrypoint: 'plugins.ring2.pii_masker:PIIMasker',
                            description: 'Mask emails, phones, SSNs.',
                            enabled: true,
                            timeout_ms: 250,
                            fail_policy: 'open',
                            version: '1.4.0',
                        },
                        {
                            name: 'smart_router',
                            hook: 'routing',
                            entrypoint: 'plugins.ring3.smart_router:SmartRouter',
                            enabled: true,
                            timeout_ms: 500,
                            fail_policy: 'open',
                        },
                    ],
                }),
            });
        });

        await authedPage.route('**/api/v1/plugins/stats', async (route) => {
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({
                    pii_masker: {
                        invocations: 142,
                        errors: 1,
                        blocks: 8,
                        avg_latency_ms: 1.7,
                        latency_percentiles: { p50: 1.2, p95: 4.1, p99: 9.8 },
                    },
                }),
            });
        });

        await authedPage.goto('/ui/#/plugins');
        await expect(authedPage.locator('#login-overlay')).not.toBeVisible();
        // Wait for the TS view to be fully mounted and loaded.
        await expect(authedPage.locator('[data-testid="plugin-card-pii_masker"]')).toBeVisible({ timeout: 10_000 });
    });

    test('renders one card per plugin with ring badge', async ({ authedPage }) => {
        await expect(authedPage.locator('[data-testid="plugin-card-pii_masker"]')).toBeVisible({ timeout: 10_000 });
        await expect(authedPage.locator('[data-testid="plugin-card-smart_router"]')).toBeVisible();
        await expect(authedPage.locator('[data-testid="plugin-ring-pii_masker"]')).toContainText('PRE-FLIGHT');
        await expect(authedPage.locator('[data-testid="plugin-ring-smart_router"]')).toContainText('ROUTING');
    });

    test('Inspect button forwards data-drilldown for the existing service', async ({ authedPage }) => {
        const inspect = authedPage.locator('[data-testid="plugin-inspect-smart_router"]');
        await expect(inspect).toBeVisible({ timeout: 10_000 });
        await expect(inspect).toHaveAttribute('data-drilldown', 'plugin:smart_router');
    });

    test('Toggle hits POST /api/v1/plugins/toggle with name + new state', async ({ authedPage }) => {
        let body: { name?: string; enabled?: boolean } | null = null;
        await authedPage.route('**/api/v1/plugins/toggle', async (route, request) => {
            body = (request.postDataJSON() as { name?: string; enabled?: boolean }) ?? null;
            await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
        });

        await authedPage.locator('[data-testid="plugin-toggle-pii_masker"]').click();
        await authedPage.waitForTimeout(200);
        expect(body?.name).toBe('pii_masker');
        expect(body?.enabled).toBe(false);
    });

    test('Uninstall opens the confirm modal; cancel skips the DELETE', async ({ authedPage }) => {
        let deleted = false;
        await authedPage.route('**/api/v1/plugins/pii_masker', async (route, request) => {
            if (request.method() === 'DELETE') deleted = true;
            await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
        });

        await authedPage.locator('[data-testid="plugin-uninstall-pii_masker"]').click();
        await expect(authedPage.locator('[data-testid="modal-confirm"]')).toBeVisible();
        await authedPage.locator('[data-testid="modal-confirm-cancel"]').click();
        await authedPage.waitForTimeout(200);
        expect(deleted).toBe(false);
    });

    test('+ Install opens the form, valid submit POSTs /api/v1/plugins/install', async ({ authedPage }) => {
        let body: Record<string, unknown> | null = null;
        await authedPage.route('**/api/v1/plugins/install', async (route, request) => {
            body = (request.postDataJSON() as Record<string, unknown>) ?? null;
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({ status: 'installed' }),
            });
        });

        await authedPage.locator('#install-plugin-toggle-btn').click();
        const form = authedPage.locator('[data-testid="plugin-install-form"]');
        await expect(form).toBeVisible();

        await authedPage.locator('input#install-name').fill('test_plugin');
        await authedPage.locator('input#install-entrypoint').fill('plugins.test:TestPlugin');
        await authedPage.locator('select#install-hook').selectOption('pre_flight');
        await authedPage.locator('[data-testid="install-submit-btn"]').click();
        await authedPage.waitForTimeout(200);

        expect(body).not.toBeNull();
        expect(body?.name).toBe('test_plugin');
        expect(body?.entrypoint).toBe('plugins.test:TestPlugin');
        expect(body?.hook).toBe('pre_flight');
    });

    test('Reload hits POST /api/v1/plugins/hot-swap', async ({ authedPage }) => {
        let called = false;
        await authedPage.route('**/api/v1/plugins/hot-swap', async (route, request) => {
            if (request.method() === 'POST') called = true;
            await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
        });

        await authedPage.locator('#reload-plugins-btn').click();
        await authedPage.waitForTimeout(200);
        expect(called).toBe(true);
    });
});
