import { test, expect } from './fixtures/auth';

/**
 * Covers the Settings view:
 *   1. Identity card surfaces auth mode + SSO status + the authenticated me grid.
 *   2. RBAC role matrix renders permissions × roles.
 *   3. Webhooks card lists configured endpoints with target badges, plus
 *      the available-event-types chip set; Test Fire button hits POST
 *      /api/v1/webhooks/test.
 *   4. Data Export card surfaces output_dir + the option badges.
 *   5. System Info renders version + endpoint.
 */
test.describe('settings view', () => {
    test.beforeEach(async ({ authedPage }) => {
        await authedPage.route('**/api/v1/version', async (route) => {
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({ version: '1.18.0-test' }),
            });
        });
        await authedPage.route('**/api/v1/service-info', async (route) => {
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({ url: 'https://proxy.local:8090' }),
            });
        });
        await authedPage.route('**/api/v1/identity/config', async (route) => {
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({ enabled: true }),
            });
        });
        await authedPage.route('**/api/v1/identity/me', async (route) => {
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({
                    authenticated: true,
                    provider: 'okta',
                    email: 'fab@example.com',
                    roles: ['admin'],
                    permissions: ['read:audit', 'write:keys'],
                }),
            });
        });
        await authedPage.route('**/api/v1/rbac/roles', async (route) => {
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({
                    admin: ['read:audit', 'write:keys'],
                    auditor: ['read:audit'],
                }),
            });
        });
        await authedPage.route('**/api/v1/webhooks', async (route) => {
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({
                    enabled: true,
                    endpoints: [{ name: 'sec-channel', target: 'slack', events: ['injection_blocked'] }],
                    event_types: ['injection_blocked', 'panic', 'budget_exceeded'],
                }),
            });
        });
        await authedPage.route('**/api/v1/export/status', async (route) => {
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({
                    enabled: true,
                    output_dir: '/var/exports',
                    scrub_pii: true,
                    compress: false,
                    files: [{ name: 'audit-2026-04-25.jsonl', size_bytes: 51200 }],
                }),
            });
        });

        await authedPage.goto('/ui/#/settings');
        await expect(authedPage.locator('#login-overlay')).not.toBeVisible();
    });

    test('Identity card surfaces auth mode, SSO status and the authenticated user', async ({ authedPage }) => {
        const card = authedPage.locator('[data-testid="settings-identity"]');
        await expect(card).toBeVisible({ timeout: 10_000 });
        await expect(card).toContainText('SSO / OIDC');
        await expect(card).toContainText('Enabled');
        await expect(card).toContainText('okta');
        await expect(card).toContainText('fab@example.com');
        await expect(card).toContainText('2 granted');
    });

    test('RBAC matrix renders permissions × roles with check / dash cells', async ({ authedPage }) => {
        const matrix = authedPage.locator('[data-testid="rbac-matrix-table"]');
        await expect(matrix).toBeVisible({ timeout: 10_000 });
        await expect(matrix).toContainText('admin');
        await expect(matrix).toContainText('auditor');
        await expect(matrix).toContainText('read:audit');
        await expect(matrix).toContainText('write:keys');
    });

    test('Webhooks card surfaces configured endpoints + Test Fire button hits the API', async ({ authedPage }) => {
        const card = authedPage.locator('[data-testid="settings-webhooks"]');
        await expect(card).toBeVisible({ timeout: 10_000 });
        await expect(card).toContainText('sec-channel');
        await expect(authedPage.locator('[data-testid="webhook-target-sec-channel"]')).toContainText('SLACK');
        await expect(card).toContainText('budget_exceeded');

        let called = false;
        await authedPage.route('**/api/v1/webhooks/test', async (route, request) => {
            if (request.method() === 'POST') called = true;
            await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
        });
        await authedPage.locator('[data-testid="test-webhook-btn"]').click();
        await authedPage.waitForTimeout(200);
        expect(called).toBe(true);
    });

    test('Data Export card surfaces output dir + option badges + recent files', async ({ authedPage }) => {
        const card = authedPage.locator('[data-testid="settings-export"]');
        await expect(card).toBeVisible({ timeout: 10_000 });
        await expect(card).toContainText('/var/exports');
        await expect(authedPage.locator('[data-testid="export-pii-badge"]')).toContainText('ON');
        await expect(authedPage.locator('[data-testid="export-compress-badge"]')).toContainText('OFF');
        await expect(authedPage.locator('[data-testid="export-files"]')).toContainText('audit-2026-04-25.jsonl');
    });

    test('System Info card surfaces the proxy version and endpoint URL', async ({ authedPage }) => {
        const card = authedPage.locator('[data-testid="settings-system-info"]');
        await expect(card).toBeVisible({ timeout: 10_000 });
        await expect(card).toContainText('1.18.0-test');
        await expect(card).toContainText('https://proxy.local:8090');
    });

    test('Config Warnings widget shows the green badge when startup checks pass', async ({ authedPage }) => {
        await authedPage.unroute('**/api/v1/config/warnings').catch(() => {});
        await authedPage.route('**/api/v1/config/warnings', async (route) => {
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({ warnings: [] }),
            });
        });
        await authedPage.reload();
        await expect(authedPage.locator('#login-overlay')).not.toBeVisible();
        const card = authedPage.locator('[data-testid="settings-config-warnings"]');
        await expect(card).toBeVisible({ timeout: 10_000 });
        await expect(authedPage.locator('[data-testid="config-warnings-ok"]')).toBeVisible();
        await expect(authedPage.locator('[data-testid="config-warnings-empty"]')).toBeVisible();
    });

    test('Config Warnings widget surfaces a row per warning when startup checks flagged drift', async ({ authedPage }) => {
        await authedPage.unroute('**/api/v1/config/warnings').catch(() => {});
        await authedPage.route('**/api/v1/config/warnings', async (route) => {
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({
                    warnings: [
                        "Endpoint 'openai' needs OPENAI_API_KEY — skipped.\n  Set in .env: OPENAI_API_KEY=your-api-key",
                        'No LLM endpoints configured — starting in ONBOARDING MODE.',
                    ],
                }),
            });
        });
        await authedPage.reload();
        await expect(authedPage.locator('#login-overlay')).not.toBeVisible();
        const list = authedPage.locator('[data-testid="config-warnings-list"]');
        await expect(list).toBeVisible({ timeout: 10_000 });
        await expect(list).toContainText('OPENAI_API_KEY');
        await expect(list).toContainText('ONBOARDING MODE');
        await expect(authedPage.locator('[data-testid="config-warnings-count"]')).toContainText(/2 warnings/);
    });
});
