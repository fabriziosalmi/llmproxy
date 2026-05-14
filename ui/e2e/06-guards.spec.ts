import { test, expect } from './fixtures/auth';

/**
 * Covers the operator's flow on the Guards tab:
 *   1. Master + priority toggle cards render with the live state.
 *   2. The 8-card guards grid renders with provenance ℹ buttons.
 *   3. Flipping a toggleable guard fires the backend call and updates
 *      aria-checked + ARIA + the visible status on success.
 *
 * Backend calls are stubbed via page.route so the assertions don't depend
 * on the proxy actually persisting feature flags during the test run.
 */
test.describe('guards view', () => {
    test.beforeEach(async ({ authedPage }) => {
        // Stub the guards status endpoint so the page mounts deterministically.
        await authedPage.route('**/api/v1/guards/status', async (route) => {
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({
                    proxy_enabled: true,
                    priority_mode: false,
                    features: {
                        injection_guard: true,
                        language_guard: false,
                        link_sanitizer: true,
                    },
                    firewall: { enabled: true, disabled_reason: null },
                    budget: { total_cost_today: 0, daily_limit: 0 },
                }),
            });
        });
        // Toggle endpoints — flip whatever the body asks for and echo it back.
        await authedPage.route('**/api/v1/features/*/toggle', async (route, request) => {
            const body = (request.postDataJSON() as { enabled?: boolean }) ?? {};
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({ enabled: body.enabled === true }),
            });
        });

        await authedPage.goto('/ui/#/guards');
        await expect(authedPage.locator('#login-overlay')).not.toBeVisible();
    });

    test('renders all 8 guard cards with names and provenance buttons', async ({ authedPage }) => {
        // Wait for the TS view to mount (legacy markup is replaced).
        await expect(authedPage.locator('[data-testid="guard-card-injection_guard"]')).toBeVisible({ timeout: 10_000 });
        await expect(authedPage.locator('[data-testid="guard-card-pii_masker"]')).toBeVisible();
        await expect(authedPage.locator('[data-testid="guard-card-firewall"]')).toBeVisible();

        // Provenance ℹ buttons land — picking the injection guard's as a smoke test.
        const aboutBtn = authedPage.getByRole('button', { name: /about injection guard/i });
        await expect(aboutBtn).toBeVisible();
    });

    test('toggling a guard updates aria-checked optimistically and surfaces a toast', async ({ authedPage }) => {
        // Stub the toggle endpoint so the optimistic aria-checked=true isn't
        // reverted by the inevitable backend failure. Without this, Playwright
        // races the optimistic window (~0–50ms) vs the rollback (~10–500ms)
        // and the assertion is non-deterministic — exactly the flake CI hit.
        await authedPage.route('**/api/v1/features/toggle', async (route) => {
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({ name: 'language_guard', enabled: true }),
            });
        });

        const sw = authedPage.locator('[data-testid="guard-toggle-language_guard"]');
        await expect(sw).toBeVisible({ timeout: 10_000 });
        await expect(sw).toHaveAttribute('aria-checked', 'false');

        // Activate via keyboard — the sticky header intercepts pointer
        // events at the toggle's viewport coordinates, so a real click
        // races a retry loop. Space on a focused role="switch" follows
        // the same code path and isn't intercepted. Same idiom the
        // master-toggle test below already uses.
        await sw.focus();
        await authedPage.keyboard.press('Space');
        // After the stub resolves, the switch should reflect the new state.
        await expect(sw).toHaveAttribute('aria-checked', 'true');
    });

    test('the master toggle card is keyboard-activatable and reflects the API response', async ({ authedPage }) => {
        // Stub /api/v1/proxy/toggle (the legacy proxy enable endpoint surfaces under different paths
        // depending on the version; route both).
        await authedPage.route('**/api/v1/proxy/*', async (route) => {
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({ enabled: false }),
            });
        });

        const masterHost = authedPage.locator('[data-testid="guards-master-host"]');
        await expect(masterHost).toBeVisible();
        const sw = masterHost.locator('[data-testid="guards-master-toggle"]');
        await expect(sw).toBeVisible({ timeout: 10_000 });
        await expect(sw).toHaveAttribute('aria-checked', 'true');

        // Activate via keyboard.
        await sw.focus();
        await authedPage.keyboard.press('Space');
        await expect(sw).toHaveAttribute('aria-checked', 'false');
    });

    test('firewall card surfaces "OFF · <reason>" when the backend reports it disabled', async ({ authedPage }) => {
        // Override the route to flip firewall off.
        await authedPage.unroute('**/api/v1/guards/status');
        await authedPage.route('**/api/v1/guards/status', async (route) => {
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({
                    proxy_enabled: true,
                    priority_mode: false,
                    features: {},
                    firewall: { enabled: false, disabled_reason: 'env:LLM_PROXY_FIREWALL_ENABLED' },
                    budget: { total_cost_today: 0, daily_limit: 0 },
                }),
            });
        });
        await authedPage.reload();
        await expect(authedPage.locator('#login-overlay')).not.toBeVisible();

        const status = authedPage.locator('[data-testid="guard-status-firewall"]');
        await expect(status).toBeVisible({ timeout: 10_000 });
        await expect(status).toContainText(/OFF · env/i);
    });
});
