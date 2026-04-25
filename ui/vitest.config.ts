import { defineConfig } from 'vitest/config';

export default defineConfig({
    test: {
        environment: 'happy-dom',
        globals: true,
        include: ['__tests__/**/*.{test,spec}.{js,ts}', '**/*.{test,spec}.{js,ts}'],
        exclude: ['node_modules', 'dist', 'public', 'e2e', 'playwright-report'],
        coverage: {
            provider: 'v8',
            reporter: ['text', 'html', 'lcov'],
            exclude: ['node_modules/', 'dist/', 'public/', 'e2e/', '__tests__/', '**/*.config.{js,ts}', '**/*.d.ts'],
        },
    },
});
