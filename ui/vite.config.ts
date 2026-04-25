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
    root: __dirname,
    base: '/ui/',
    publicDir: 'public',
    build: {
        outDir: 'dist',
        emptyOutDir: true,
        sourcemap: true,
        rollupOptions: {
            input: {
                main: resolve(__dirname, 'index.html'),
                chat: resolve(__dirname, 'chat.html'),
                oauth: resolve(__dirname, 'oauth-callback.html'),
            },
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
