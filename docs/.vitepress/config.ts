import { defineConfig } from 'vitepress'

export default defineConfig({
  base: '/llmproxy/',
  srcExclude: ['**/node_modules/**', '**/venv/**', '**/dist/**'],
  title: 'LLMProxy',
  description: 'LLM Security Gateway — Security-first proxy for Large Language Models',
  head: [
    ['link', { rel: 'preconnect', href: 'https://fonts.googleapis.com' }],
    ['link', { rel: 'preconnect', href: 'https://fonts.gstatic.com', crossorigin: '' }],
    ['link', { href: 'https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap', rel: 'stylesheet' }],
    ['link', { rel: 'icon', type: 'image/svg+xml', href: '/llmproxy/favicon.svg' }],
    ['meta', { name: 'theme-color', content: '#f43f5e' }],
    ['meta', { property: 'og:type', content: 'website' }],
    ['meta', { property: 'og:title', content: 'LLMProxy — LLM Security Gateway' }],
    ['meta', { property: 'og:description', content: 'Security-first proxy for Large Language Models with multi-provider support, ring-based plugin pipeline, and real-time SOC dashboard.' }],
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
      { text: 'SOC', link: '/soc/overview' },
      {
        text: 'Reference',
        items: [
          { text: 'Configuration', link: '/reference/config' },
          { text: 'Endpoints', link: '/reference/endpoints' },
          { text: 'Metrics', link: '/reference/metrics' },
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
            { text: 'ASGI Firewall', link: '/security/firewall' },
            { text: 'PII Detection', link: '/security/pii-detection' },
            { text: 'Injection Scoring', link: '/security/injection-scoring' },
            { text: 'Identity & SSO', link: '/security/identity' },
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
      '/soc/': [
        {
          text: 'SOC Dashboard',
          items: [
            { text: 'Overview', link: '/soc/overview' },
            { text: 'Threats', link: '/soc/threats' },
            { text: 'Guards', link: '/soc/guards' },
            { text: 'Plugins Panel', link: '/soc/plugins' },
            { text: 'Models', link: '/soc/models' },
            { text: 'Analytics', link: '/soc/analytics' },
            { text: 'Endpoints', link: '/soc/endpoints' },
            { text: 'Live Logs', link: '/soc/logs' },
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
