import { defineConfig, devices } from '@playwright/test';

const PORT = Number(process.env.LLMPROXY_PORT ?? 8090);
const BASE_URL = process.env.LLMPROXY_BASE_URL ?? `http://localhost:${PORT}`;

// Set LLMPROXY_SKIP_WEB_SERVER=1 when the backend is already running
// (e.g. CI step started it explicitly).
const skipWebServer = process.env.LLMPROXY_SKIP_WEB_SERVER === '1';

export default defineConfig({
    testDir: './e2e',
    timeout: 30_000,
    expect: { timeout: 5_000 },
    fullyParallel: true,
    forbidOnly: !!process.env.CI,
    retries: process.env.CI ? 2 : 0,
    workers: process.env.CI ? 2 : undefined,
    reporter: process.env.CI ? [['github'], ['html', { open: 'never' }]] : 'list',
    use: {
        baseURL: BASE_URL,
        trace: 'retain-on-failure',
        video: 'retain-on-failure',
        screenshot: 'only-on-failure',
    },
    projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
    webServer: skipWebServer
        ? undefined
        : {
              command:
                  process.platform === 'win32'
                      ? 'cmd /c "cd .. && .venv\\Scripts\\python.exe main.py"'
                      : 'bash -c "cd .. && . venv/bin/activate && python main.py"',
              url: `${BASE_URL}/health`,
              reuseExistingServer: !process.env.CI,
              timeout: 60_000,
              stdout: 'pipe',
              stderr: 'pipe',
          },
});
