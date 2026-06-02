import { test, expect } from './fixtures/auth';

/**
 * Covers the "I just installed the proxy, let me wire up my first endpoint"
 * journey. Asserts the form's client-side validation as well, because that's
 * the second thing operators hit and the regression risk is high (the
 * registry view is one of the heaviest legacy components).
 *
 * Selectors deliberately use data-testid so the same spec passes against
 * both the legacy markup (1.14.0 and earlier) and the TS-mounted form
 * shipped in 1.15.0 — the testids are stable across both layers.
 */
test.describe('add endpoint', () => {
    test.beforeEach(async ({ authedPage }) => {
        await authedPage.goto('/ui/#/endpoints');
        await expect(authedPage.locator('#login-overlay')).not.toBeVisible();

        // Wait for the TS view to be fully mounted/hydrated.
        await expect(authedPage.locator('[data-testid="add-endpoint-form"]')).toBeAttached({ timeout: 10_000 });

        // Ensure form is closed at the start of each test to prevent SPA state bleed.
        const form = authedPage.locator('[data-testid="add-endpoint-form"]');
        if (await form.isVisible()) {
            await authedPage.locator('[data-testid="ep-cancel-btn"]').click();
            await expect(form).toBeHidden();
        }
    });

    test('refuses an invalid id with an inline error and keeps the form open', async ({ authedPage }) => {
        await authedPage.locator('#add-endpoint-toggle').click();
        const form = authedPage.locator('[data-testid="add-endpoint-form"]');
        await expect(form).toBeVisible();

        // Submit with empty fields — both errors should surface.
        await authedPage.locator('[data-testid="ep-add-btn"]').click();

        const nameErr = authedPage.locator('#ep-name-err');
        const urlErr = authedPage.locator('#ep-url-err');
        await expect(nameErr).toBeVisible();
        await expect(nameErr).toContainText('Required');
        await expect(urlErr).toBeVisible();

        // Form is still open — submission was rejected client-side.
        await expect(form).toBeVisible();
    });

    test('rejects a non-http URL', async ({ authedPage }) => {
        await authedPage.locator('#add-endpoint-toggle').click();
        await authedPage.locator('input#ep-name').fill('e2e-bad-url');
        await authedPage.locator('input#ep-url').fill('ftp://example.com/v1');
        await authedPage.locator('[data-testid="ep-add-btn"]').click();
        await expect(authedPage.locator('#ep-url-err')).toBeVisible();
    });

    test('successfully posts a new endpoint and renders it in the registry', async ({ authedPage }) => {
        const id = `e2e-test-${Date.now()}`;

        // Stub the registry POST so we never depend on backend persistence
        // behavior (or bleed test endpoints into the local DB on dev runs).
        await authedPage.route('**/api/v1/registry', async (route, request) => {
            if (request.method() === 'POST') {
                await route.fulfill({
                    status: 201,
                    contentType: 'application/json',
                    body: JSON.stringify({ id, status: 'created' }),
                });
                return;
            }
            // GET — return our newly-added endpoint plus an empty rest of the registry.
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify([
                    {
                        id,
                        url: 'https://api.openai.com/v1',
                        provider: 'openai',
                        status: 'Live',
                        circuit_state: 'closed',
                        priority: 0,
                        models: ['gpt-4o-mini'],
                        enabled: true,
                        healthy: true,
                    },
                ]),
            });
        });

        await authedPage.locator('#add-endpoint-toggle').click();
        await authedPage.locator('input#ep-name').fill(id);
        await authedPage.locator('input#ep-url').fill('https://api.openai.com/v1');
        await authedPage.locator('select#ep-provider').selectOption('openai');
        await authedPage.locator('input#ep-models').fill('gpt-4o-mini');
        await authedPage.locator('[data-testid="ep-add-btn"]').click();

        // Form collapses; row appears in the registry container.
        await expect(authedPage.locator('[data-testid="add-endpoint-form"]')).toBeHidden();
        await expect(authedPage.locator('#registry-container')).toContainText(id);
    });
});
