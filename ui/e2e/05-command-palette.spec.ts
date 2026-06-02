import { test, expect } from './fixtures/auth';

/**
 * Covers Sprint 2's command palette as a control surface:
 *   1. Cmd+K opens it; Escape closes it.
 *   2. Typing filters the visible commands; Enter activates the highlighted one.
 *   3. Typing `>` switches the palette into jump-to mode and surfaces kind hints.
 *
 * Authenticated via the auth fixture so the palette's data sources (registry,
 * /v1/models, /api/v1/plugins) can be reached without 401s.
 */
test.describe('command palette', () => {
    test.beforeEach(async ({ authedPage }) => {
        await authedPage.goto('/ui/#/threats');
        await expect(authedPage.locator('#login-overlay')).not.toBeVisible();
        // Wait for the app to be fully booted/hydrated (timerange slot rendered).
        await expect(authedPage.locator('[data-tr-root]')).toBeAttached({ timeout: 10_000 });
    });

    test('Cmd+K opens the palette and Escape closes it', async ({ authedPage }) => {
        const overlay = authedPage.locator('#cmd-palette-overlay');
        await expect(overlay).toBeHidden();

        await authedPage.keyboard.press('Meta+k');
        await expect(overlay).toBeVisible();
        await expect(authedPage.locator('#cmd-input')).toBeFocused();

        await authedPage.keyboard.press('Escape');
        await expect(overlay).toBeHidden();
    });

    test('Ctrl+K works as the cross-platform alias', async ({ authedPage }) => {
        const overlay = authedPage.locator('#cmd-palette-overlay');
        await authedPage.keyboard.press('Control+k');
        await expect(overlay).toBeVisible();
        await authedPage.keyboard.press('Control+k');
        await expect(overlay).toBeHidden();
    });

    test('typing filters commands and Enter navigates to the chosen tab', async ({ authedPage }) => {
        await authedPage.keyboard.press('Meta+k');
        await authedPage.locator('#cmd-input').fill('guards');

        const list = authedPage.locator('#cmd-list');
        await expect(list).toContainText(/security guards/i);
        // The first matching result is auto-selected (aria-selected=true).
        await expect(list.locator('[role="option"]').first()).toHaveAttribute('aria-selected', 'true');

        await authedPage.keyboard.press('Enter');
        // Palette closes…
        await expect(authedPage.locator('#cmd-palette-overlay')).toBeHidden();
        // …and the URL hash flips to the chosen view.
        await expect.poll(() => authedPage.url()).toMatch(/#\/guards$/);
    });

    test('typing `>` enters jump-to mode and lists the four kinds', async ({ authedPage }) => {
        await authedPage.keyboard.press('Meta+k');
        await authedPage.locator('#cmd-input').fill('>');

        const list = authedPage.locator('#cmd-list');
        await expect(list).toContainText(/jump to an endpoint drilldown/i);
        await expect(list).toContainText(/jump to a model drilldown/i);
        await expect(list).toContainText(/jump to a plugin drilldown/i);
        await expect(list).toContainText(/open a request audit entry/i);
    });

    test('the empty-state message appears when no command matches', async ({ authedPage }) => {
        await authedPage.keyboard.press('Meta+k');
        await authedPage.locator('#cmd-input').fill('zzzz-nothing-matches-this');
        await expect(authedPage.locator('#cmd-list')).toContainText(/no matching commands/i);
    });
});
