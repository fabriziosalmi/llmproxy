import { test, expect } from './fixtures/auth';

/**
 * Covers the Models view:
 *   1. KPI tiles populate from /v1/models (Active / Providers / Embedding).
 *   2. Models render in two tables (chat + embedding) with EMB badges.
 *   3. Inspect forwards data-drilldown="model:<id>".
 *   4. Search filters both tables; an unmatched query shows the empty state.
 */
test.describe('models view', () => {
    test.beforeEach(async ({ authedPage }) => {
        await authedPage.route('**/v1/models', async (route) => {
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({
                    data: [
                        { id: 'gpt-4o-mini', owned_by: 'openai', object: 'model' },
                        { id: 'claude-sonnet-4', owned_by: 'anthropic', object: 'model' },
                        { id: 'gemini-2.5-pro', owned_by: 'google', object: 'model' },
                        { id: 'text-embedding-3-small', owned_by: 'openai', object: 'model' },
                        { id: 'bge-large-en-v1.5', owned_by: 'mistral', object: 'model' },
                    ],
                }),
            });
        });

        await authedPage.goto('/ui/#/models');
        await expect(authedPage.locator('#login-overlay')).not.toBeVisible();
    });

    test('renders KPI tiles populated from /v1/models', async ({ authedPage }) => {
        const active = authedPage.locator('[data-testid="kpi-active-models"]');
        await expect(active).toBeVisible({ timeout: 10_000 });
        await expect(active).toContainText('5');

        const providers = authedPage.locator('[data-testid="kpi-providers"]');
        await expect(providers).toContainText('4');

        const embedding = authedPage.locator('[data-testid="kpi-embedding-models"]');
        await expect(embedding).toContainText('2');
    });

    test('embedding rows carry an EMB badge; chat rows do not', async ({ authedPage }) => {
        await expect(authedPage.locator('[data-testid="model-emb-text-embedding-3-small"]')).toBeVisible({
            timeout: 10_000,
        });
        await expect(authedPage.locator('[data-testid="model-emb-bge-large-en-v1.5"]')).toBeVisible();
        await expect(authedPage.locator('[data-testid="model-emb-gpt-4o-mini"]')).toHaveCount(0);
    });

    test('Inspect button forwards data-drilldown for the existing drilldown service', async ({ authedPage }) => {
        const inspect = authedPage.locator('[data-testid="model-inspect-claude-sonnet-4"]');
        await expect(inspect).toBeVisible({ timeout: 10_000 });
        await expect(inspect).toHaveAttribute('data-drilldown', 'model:claude-sonnet-4');
    });

    test('search filters both chat and embedding tables; empty query brings everything back', async ({
        authedPage,
    }) => {
        await expect(authedPage.locator('[data-testid="model-inspect-gpt-4o-mini"]')).toBeVisible({ timeout: 10_000 });

        const search = authedPage.locator('input#models-search');
        await search.fill('embed');
        // Debounced 150ms.
        await authedPage.waitForTimeout(250);
        await expect(authedPage.locator('[data-testid="model-inspect-text-embedding-3-small"]')).toBeVisible();
        await expect(authedPage.locator('[data-testid="model-inspect-gpt-4o-mini"]')).toHaveCount(0);

        await search.fill('');
        await authedPage.waitForTimeout(250);
        await expect(authedPage.locator('[data-testid="model-inspect-gpt-4o-mini"]')).toBeVisible();
    });

    test('an unmatched query shows the empty-state surface', async ({ authedPage }) => {
        await expect(authedPage.locator('[data-testid="model-inspect-gpt-4o-mini"]')).toBeVisible({ timeout: 10_000 });
        const search = authedPage.locator('input#models-search');
        await search.fill('zzz-no-such-model');
        await authedPage.waitForTimeout(250);
        await expect(authedPage.locator('[data-testid="models-no-match"]')).toBeVisible();
    });
});
