import type { Page } from '@playwright/test';

/**
 * Replace `window.EventSource` with a fake that can be driven from tests.
 * Must be installed via `page.addInitScript` BEFORE any page script runs so
 * the feed picks up the fake when it constructs its source.
 *
 * After install, drive emissions from the test via:
 *   await page.evaluate((data) => window.__sseEmit(data), { level: 'SECURITY', ... });
 */
export async function installSseMock(page: Page): Promise<void> {
    await page.addInitScript(() => {
        const installed: {
            onmessage?: (ev: MessageEvent) => void;
            onopen?: () => void;
            onerror?: () => void;
            closed?: boolean;
        }[] = [];

        class FakeEventSource {
            public onmessage: ((ev: MessageEvent) => void) | null = null;
            public onopen: (() => void) | null = null;
            public onerror: (() => void) | null = null;
            public readyState = 0;
            public closed = false;
            constructor(public readonly url: string) {
                installed.push(this);
                queueMicrotask(() => {
                    this.readyState = 1;
                    this.onopen?.();
                });
            }
            close(): void {
                this.closed = true;
                this.readyState = 2;
            }
            addEventListener(): void {
                /* not used by the feed today */
            }
            removeEventListener(): void {
                /* not used */
            }
            dispatchEvent(): boolean {
                return true;
            }
        }

        // @ts-expect-error — overriding the global on purpose for tests
        window.EventSource = FakeEventSource;

        (window as unknown as { __sseEmit: (data: unknown) => void }).__sseEmit = (data) => {
            // Emit to all currently-open fakes — covers reconnect cases.
            for (const fake of installed) {
                if (fake.closed) continue;
                fake.onmessage?.(new MessageEvent('message', { data: JSON.stringify(data) }));
            }
        };

        (window as unknown as { __sseError: () => void }).__sseError = () => {
            for (const fake of installed) fake.onerror?.();
        };
    });
}
