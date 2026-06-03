import { test, expect } from './fixtures/auth';
import { installSseMock } from './fixtures/sseMock';

/**
 * Covers the operator's investigation flow on the Threats tab:
 *  1. Land on Threats with an authenticated session.
 *  2. KPI tiles render with their provenance ℹ buttons.
 *  3. A live event arrives via SSE → the feed shows a row with Investigate /
 *     Explain / Mute actions.
 *  4. Investigate forwards to the existing drilldown service via
 *     data-drilldown attribute. Explain forwards to the explain pane via
 *     data-explain. Mute persists to localStorage and hides the event.
 *
 * EventSource is stubbed via `installSseMock` so the test is deterministic
 * and does not require the backend to actually produce a security event.
 */
test.describe('threats drilldown', () => {
    test.beforeEach(async ({ authedPage }) => {
        await installSseMock(authedPage);
        await authedPage.goto('/ui/#/threats');
        await expect(authedPage.locator('#login-overlay')).not.toBeVisible();
        await authedPage.getByRole('button', { name: 'Investigate' }).click();
        await authedPage.evaluate(() =>
            (window as unknown as { __sseWaitForClient: (timeoutMs?: number) => Promise<void> }).__sseWaitForClient(
                8000
            )
        );
    });

    test('KPI grid renders with provenance ℹ buttons', async ({ authedPage }) => {
        // Wait for the TS-built tile (data-testid set on each MetricTile) to mount.
        const requestsTile = authedPage.locator('[data-testid="kpi-requests"]');
        await expect(requestsTile).toBeVisible({ timeout: 10_000 });

        // Provenance buttons are present and carry an aria-label tied to the metric.
        const aboutBtn = requestsTile.getByRole('button', { name: /about requests today/i });
        await expect(aboutBtn).toBeVisible();
        // Tooltip content lives on the title attribute (native browser tooltip).
        const title = await aboutBtn.getAttribute('title');
        expect(title).toContain('llm_proxy_requests_total');
    });

    test('an SSE event renders an actionable row with Investigate / Explain / Mute', async ({ authedPage }) => {
        // Drive a security event into the live feed via the mocked EventSource.
        await authedPage.evaluate(() =>
            (window as unknown as { __sseEmit: (data: unknown) => void }).__sseEmit({
                level: 'SECURITY',
                message: 'WAF rejected prompt-injection attempt on /v1/chat/completions',
                req_id: 'req-e2e-123',
                signature: 'rule_injection_v3',
                timestamp: '12:34:56',
            })
        );

        const list = authedPage.locator('[data-testid="threat-feed-list"]');
        await expect(list).toContainText('WAF rejected');

        const investigate = authedPage.locator('[data-testid="threat-investigate-req-e2e-123"]');
        await expect(investigate).toBeVisible();
        await expect(investigate).toHaveAttribute('data-drilldown', 'request:req-e2e-123');

        const explain = authedPage.locator('[data-testid="threat-explain-rule_injection_v3"]');
        await expect(explain).toBeVisible();
        await expect(explain).toHaveAttribute('data-explain', 'rule:rule_injection_v3');

        const muteBtn = authedPage.locator('[data-testid="threat-mute-SECURITY:rule_injection_v3"]');
        await expect(muteBtn).toBeVisible();
        await expect(muteBtn).toHaveAttribute('aria-pressed', 'false');
    });

    test('mute hides the event and persists to localStorage', async ({ authedPage }) => {
        const event = {
            level: 'WARNING',
            message: 'Auth failure for fab',
            signature: 'auth_bad',
            timestamp: '12:01:02',
        };
        await authedPage.evaluate(
            (d) => (window as unknown as { __sseEmit: (data: unknown) => void }).__sseEmit(d),
            event
        );

        const list = authedPage.locator('[data-testid="threat-feed-list"]');
        await expect(list).toContainText('Auth failure for fab');

        const mute = authedPage.locator('[data-testid="threat-mute-WARNING:auth_bad"]');
        await mute.click();

        // The (now muted) event is removed from the visible list and replaced by
        // the empty-state surface. The muted set persists to localStorage.
        await expect(authedPage.locator('[data-testid="threat-feed-empty"]')).toBeVisible();

        const stored = await authedPage.evaluate(() => window.localStorage.getItem('llmproxy:muted-threats'));
        expect(stored).toBeTruthy();
        expect(JSON.parse(stored ?? '[]')).toContain('WARNING:auth_bad');

        // A second event of a different category still gets through.
        await authedPage.evaluate(() =>
            (window as unknown as { __sseEmit: (data: unknown) => void }).__sseEmit({
                level: 'CRITICAL',
                message: 'Budget exceeded',
                signature: 'budget_panic',
            })
        );
        await expect(list).toContainText('Budget exceeded');
    });
});
