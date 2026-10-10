import { defineConfig } from 'vitepress'

export default defineConfig({
  base: '/llmproxy/',
  // The hostname carries the base path on purpose: VitePress joins it with each
  // page's route, so without it every URL in the sitemap would point at a 404.
  sitemap: { hostname: 'https://fabriziosalmi.github.io/llmproxy/' },
  // specs/ holds design notes for work that is not built; they stay in the
  // repository and off the site.
  srcExclude: ['**/node_modules/**', '**/venv/**', '**/dist/**', 'specs/**'],
  title: 'LLMProxy',
  description: 'A self-hosted, OpenAI-compatible LLM gateway with an audit log you can verify.',
  head: [
    // Self-hosted fonts (docs/public/fonts) — no request to Google.
    ['link', { href: '/llmproxy/fonts/fonts.css', rel: 'stylesheet' }],
    ['link', { rel: 'icon', type: 'image/svg+xml', href: '/llmproxy/favicon.svg' }],
    ['link', { rel: 'icon', type: 'image/png', sizes: '32x32', href: '/llmproxy/favicon.png' }],
    ['meta', { name: 'theme-color', content: '#f43f5e' }],
    ['meta', { property: 'og:type', content: 'website' }],
    ['meta', { property: 'og:title', content: 'LLMProxy' }],
    ['meta', { property: 'og:description', content: 'A self-hosted, OpenAI-compatible LLM gateway. It records every request it handles in a hash chain you can verify, applies policy to what a response may do, and runs as one process on your infrastructure.' }],
  ],

  cleanUrls: true,

  themeConfig: {
    logo: '/logo.svg',
    siteTitle: 'LLMProxy',

    nav: [
      { text: 'Guide', link: '/guide/what-is-llmproxy' },
      { text: 'Security', link: '/security/overview' },
      { text: 'Plugins', link: '/plugins/overview' },
      { text: 'API', link: '/api/proxy' },
      { text: 'Admin UI', link: '/admin-ui/overview' },
      {
        text: 'Reference',
        items: [
          { text: 'Configuration', link: '/reference/config' },
          { text: 'Endpoints', link: '/reference/endpoints' },
          { text: 'Metrics', link: '/reference/metrics' },
          { text: 'Performance', link: '/reference/performance' },
        ]
      }
    ],

    sidebar: {
      '/guide/': [
        {
          text: 'Introduction',
          items: [
            { text: 'What is LLMProxy?', link: '/guide/what-is-llmproxy' },
            { text: 'Quick Start', link: '/guide/quickstart' },
          ]
        },
        {
          text: 'Setup',
          items: [
            { text: 'Configuration', link: '/guide/configuration' },
            { text: 'Deployment', link: '/guide/deployment' },
          ]
        }
      ],
      '/security/': [
        {
          text: 'Security',
          items: [
            { text: 'Overview', link: '/security/overview' },
            { text: 'Audit Log', link: '/security/audit-log' },
            { text: 'Tool Policy', link: '/security/tool-policy' },
            { text: 'ASGI Firewall', link: '/security/firewall' },
            { text: 'Injection Scoring', link: '/security/injection-scoring' },
            { text: 'PII Detection', link: '/security/pii-detection' },
            { text: 'Identity & SSO', link: '/security/identity' },
            { text: 'SIEM Export', link: '/security/siem-export' },
          ]
        },
        {
          text: 'Optional Checks',
          items: [
            { text: 'FQDN Risk Scoring', link: '/security/fqdn-risk-scoring' },
            { text: 'AI Dependency Guard', link: '/security/slopsquatting-guard' },
          ]
        },
        {
          text: 'Evidence',
          items: [
            { text: 'Threat Model', link: '/security/threat-model' },
            { text: 'Detection Benchmark', link: '/security/benchmark' },
            { text: 'Regression Corpus', link: '/security/regression-corpus' },
          ]
        }
      ],
      '/plugins/': [
        {
          text: 'Plugin Engine',
          items: [
            { text: 'Overview', link: '/plugins/overview' },
            { text: 'Plugin SDK', link: '/plugins/sdk' },
            { text: 'Marketplace Plugins', link: '/plugins/marketplace' },
            { text: 'WASM Plugins', link: '/plugins/wasm' },
            { text: 'Developing Plugins', link: '/plugins/developing' },
          ]
        }
      ],
      '/api/': [
        {
          text: 'API Reference',
          items: [
            { text: 'Model Proxy', link: '/api/proxy' },
            { text: 'Admin & Registry', link: '/api/admin' },
            { text: 'Identity & SSO', link: '/api/identity' },
            { text: 'Plugins', link: '/api/plugins' },
          ]
        }
      ],
      '/admin-ui/': [
        {
          text: 'Admin UI',
          items: [
            { text: 'Overview', link: '/admin-ui/overview' },
            { text: 'Home (Threats)', link: '/admin-ui/threats' },
            { text: 'Guards', link: '/admin-ui/guards' },
            { text: 'Plugins', link: '/admin-ui/plugins' },
            { text: 'Models', link: '/admin-ui/models' },
            { text: 'Analytics', link: '/admin-ui/analytics' },
            { text: 'Endpoints', link: '/admin-ui/endpoints' },
            { text: 'Live Logs', link: '/admin-ui/logs' },
            { text: 'Contributing', link: '/admin-ui/contributing' },
          ]
        }
      ],
      '/reference/': [
        {
          text: 'Reference',
          items: [
            { text: 'Configuration', link: '/reference/config' },
            { text: 'Endpoints', link: '/reference/endpoints' },
            { text: 'Metrics', link: '/reference/metrics' },
          { text: 'Performance', link: '/reference/performance' },
          ]
        }
      ],
    },

    socialLinks: [
      { icon: 'github', link: 'https://github.com/fabriziosalmi/llmproxy' }
    ],

    footer: {
      message: 'MIT License',
      copyright: 'Copyright 2026 Fabrizio Salmi'
    },

    search: {
      provider: 'local'
    },

    editLink: {
      pattern: 'https://github.com/fabriziosalmi/llmproxy/edit/main/docs/:path',
      text: 'Edit this page on GitHub'
    },
  },

  markdown: {
    lineNumbers: true
  }
})
