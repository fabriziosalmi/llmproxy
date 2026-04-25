import { test, expect } from './fixtures/auth';

/**
 * Covers the "I just installed the proxy, let me wire up my first endpoint"
 * journey. Asserts the form's client-side validation as well, because that's
 * the second thing operators hit and the regression risk is high (the
 * registry view is one of the heaviest legacy components).
 */
test.describe('add endpoint', () => {
    test.beforeEach(async ({ authedPage }) => {
        await authedPage.goto('/ui/#/endpoints');
        // The login overlay should have stayed hidden because the auth fixture
        // pre-seeded localStorage with a valid token.
        await expect(authedPage.locator('#login-overlay')).not.toBeVisible();
    });

    test('refuses an invalid id with an inline error and keeps the form open', async ({ authedPage }) => {
        await authedPage.locator('#add-endpoint-toggle').click();
        await expect(authedPage.locator('#add-endpoint-form')).toBeVisible();

        // Submit with empty fields — both errors should surface.
        await authedPage.locator('#ep-add-btn').click();

        const nameErr = authedPage.locator('#ep-name-err');
        const urlErr = authedPage.locator('#ep-url-err');
        await expect(nameErr).toBeVisible();
        await expect(nameErr).toContainText('Required');
        await expect(urlErr).toBeVisible();

        // Form is still open — submission was rejected client-side.
        await expect(authedPage.locator('#add-endpoint-form')).toBeVisible();
    });

    test('rejects a non-http URL', async ({ authedPage }) => {
        await authedPage.locator('#add-endpoint-toggle').click();
        await authedPage.locator('#ep-name').fill('e2e-bad-url');
        await authedPage.locator('#ep-url').fill('ftp://example.com/v1');
        await authedPage.locator('#ep-add-btn').click();
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
                        priority: 0,
                        models: ['gpt-4o-mini'],
                        enabled: true,
                        healthy: true,
                    },
                ]),
            });
        });

        await authedPage.locator('#add-endpoint-toggle').click();
        await authedPage.locator('#ep-name').fill(id);
        await authedPage.locator('#ep-url').fill('https://api.openai.com/v1');
        await authedPage.locator('#ep-provider').selectOption('openai');
        await authedPage.locator('#ep-models').fill('gpt-4o-mini');
        await authedPage.locator('#ep-add-btn').click();

        // Form collapses; toast confirms; row appears in the registry container.
        await expect(authedPage.locator('#add-endpoint-form')).toBeHidden();
        await expect(authedPage.locator('#registry-container')).toContainText(id);
    });
});
