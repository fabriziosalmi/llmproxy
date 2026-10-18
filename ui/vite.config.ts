import { defineConfig } from 'vite';
import { resolve } from 'node:path';

const BACKEND = process.env.LLMPROXY_BACKEND ?? 'http://localhost:8090';

const PROXY_PREFIXES = [
    '/v1',
    '/admin',
    '/api',
    '/health',
    '/identity',
    '/registry',
    '/plugins',
    '/telemetry',
    '/gdpr',
    '/metrics',
    '/oauth',
];

const proxyConfig = Object.fromEntries(
    PROXY_PREFIXES.map((p) => [p, { target: BACKEND, changeOrigin: true, ws: true }])
);

// FastAPI mounts the built UI at `/ui`. Mirror that in dev so window.location.origin
// + same-origin fetches behave identically across dev and prod.
export default defineConfig({
    root: import.meta.dirname,
    base: '/ui/',
    publicDir: 'public',
    build: {
        outDir: 'dist',
        emptyOutDir: true,
        sourcemap: true,
        rolldownOptions: {
            input: {
                main: resolve(import.meta.dirname, 'index.html'),
                chat: resolve(import.meta.dirname, 'chat.html'),
                oauth: resolve(import.meta.dirname, 'oauth-callback.html'),
            },
            // Rolldown merges shared code, its runtime helpers included, into an entry chunk
            // unless entry signatures are strict. Here it picked the `chat` entry, so the
            // dashboard imported that entry and ran chat.js's page setup on index.html, which
            // throws before main.js shows the login overlay. Strict keeps shared code in common
            // chunks, so each page runs only its own entry.
            preserveEntrySignatures: 'strict',
        },
    },
    server: {
        port: 5173,
        strictPort: true,
        proxy: proxyConfig,
    },
    preview: {
        port: 5174,
        proxy: proxyConfig,
    },
});
