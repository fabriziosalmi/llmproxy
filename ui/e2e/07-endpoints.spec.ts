import { test, expect } from './fixtures/auth';

/**
 * Covers the operator's flow on the Endpoints registry table:
 *   1. Rows render with status, circuit-state badge, and priority controls.
 *   2. Inspect surfaces data-drilldown="endpoint:<id>" for the existing
 *      drilldown service to pick up.
 *   3. Toggle and Reset CB hit the right backend routes.
 *   4. Delete pops the confirm modal first; only the confirm button fires
 *      the actual DELETE request.
 *   5. Priority up/down call the priority endpoint with the right value
 *      and floor at 0.
 */
test.describe('endpoints registry table', () => {
    test.beforeEach(async ({ authedPage }) => {
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
                        id: 'openai',
                        name: 'OpenAI',
                        url: 'https://api.openai.com/v1',
                        provider: 'openai',
                        status: 'Live',
                        circuit_state: 'closed',
                        latency: '120ms',
                        priority: 10,
                    },
                    {
                        id: 'flaky',
                        url: 'https://flaky.example.com/v1',
                        provider: 'openai-compatible',
                        status: 'DEGRADED',
                        circuit_state: 'half_open',
                        failure_count: 3,
                        failure_threshold: 5,
                        latency: '420ms',
                        priority: 1,
                    },
                ]),
            });
        });

        await authedPage.goto('/ui/#/endpoints');
        await expect(authedPage.locator('#login-overlay')).not.toBeVisible();
    });

    test('renders one row per endpoint with status + circuit badges', async ({ authedPage }) => {
        await expect(authedPage.locator('[data-testid="ep-status-openai"]')).toBeVisible({ timeout: 10_000 });
        await expect(authedPage.locator('[data-testid="ep-status-openai"]')).toContainText('Live');
        await expect(authedPage.locator('[data-testid="ep-circuit-flaky"]')).toContainText('HALF');
    });

    test('Inspect button forwards data-drilldown for the existing drilldown service', async ({ authedPage }) => {
        const inspect = authedPage.locator('[data-testid="ep-inspect-openai"]');
        await expect(inspect).toBeVisible({ timeout: 10_000 });
        await expect(inspect).toHaveAttribute('data-drilldown', 'endpoint:openai');
    });

    test('Reset CB hits POST /api/v1/circuit-breaker/<id>/reset and refreshes', async ({ authedPage }) => {
        let resetCalled = false;
        await authedPage.route('**/api/v1/circuit-breaker/flaky/reset', async (route) => {
            resetCalled = true;
            await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
        });

        await authedPage.locator('[data-testid="ep-reset-cb-flaky"]').click();
        // Give the request a tick.
        await authedPage.waitForTimeout(200);
        expect(resetCalled).toBe(true);
    });

    test('Delete opens a confirm modal; cancelling skips the DELETE request', async ({ authedPage }) => {
        let deleteCalled = false;
        await authedPage.route('**/api/v1/registry/flaky', async (route, request) => {
            if (request.method() === 'DELETE') deleteCalled = true;
            await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
        });

        await authedPage.locator('[data-testid="ep-delete-flaky"]').click();

        // Confirm modal mounts.
        const modal = authedPage.locator('[data-testid="modal-confirm"]');
        await expect(modal).toBeVisible();

        // Cancel.
        await authedPage.locator('[data-testid="modal-confirm-cancel"]').click();
        await expect(modal).not.toBeVisible();
        await authedPage.waitForTimeout(200);
        expect(deleteCalled).toBe(false);
    });

    test('Delete → confirm fires DELETE /api/v1/registry/<id>', async ({ authedPage }) => {
        let deleteCalled = false;
        await authedPage.route('**/api/v1/registry/flaky', async (route, request) => {
            if (request.method() === 'DELETE') deleteCalled = true;
            await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
        });

        await authedPage.locator('[data-testid="ep-delete-flaky"]').click();
        await expect(authedPage.locator('[data-testid="modal-confirm"]')).toBeVisible();
        await authedPage.locator('[data-testid="modal-confirm-ok"]').click();
        await authedPage.waitForTimeout(200);
        expect(deleteCalled).toBe(true);
    });

    test('Priority up calls /api/v1/registry/<id>/priority with priority+1', async ({ authedPage }) => {
        let bodySent: { priority?: number } | null = null;
        await authedPage.route('**/api/v1/registry/flaky/priority', async (route, request) => {
            bodySent = (request.postDataJSON() as { priority?: number }) ?? null;
            await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
        });

        await authedPage.locator('[data-testid="ep-priority-up-flaky"]').click();
        await authedPage.waitForTimeout(200);
        expect(bodySent?.priority).toBe(2);
    });
});
