---
layout: home

hero:
  name: "LLMProxy"
  text: "AI Security Gateway"
  tagline: Security-first proxy for Large Language Models with multi-provider routing, ring-based plugin pipeline, and real-time threat monitoring.
  actions:
    - theme: brand
      text: Get Started
      link: /guide/quickstart
    - theme: alt
      text: View on GitHub
      link: https://github.com/fabriziosalmi/llmproxy

features:
  - title: Security Pipeline
    details: 10-layer defense with ASGI firewall, injection scoring, PII masking (Presidio NLP + regex), and multi-turn trajectory detection.
  - title: Ring Plugin Engine
    details: 5-ring pipeline (Ingress, Pre-Flight, Routing, Post-Flight, Background) with 14 marketplace plugins and WASM sandbox support.
  - title: 15 Providers
    details: OpenAI, Anthropic, Google, Azure, Ollama, Groq, Together, Mistral, DeepSeek, xAI, Perplexity, OpenRouter, Fireworks, SambaNova.
  - title: SOC Dashboard
    details: Real-time Security Operations Center with threat monitoring, guard controls, plugin management, spend analytics, and live terminal logs.
  - title: PII Detection
    details: Dual-mode PII masking with Presidio NLP engine (18 entity types) and regex fallback. Vault-based tokenization with reversible demasking.
  - title: Smart Routing
    details: EMA-weighted latency routing, cross-provider fallback chains, model aliases, budget-aware downgrading, and A/B model experimentation.
---

<style>
.architecture-section {
  margin-top: 80px;
  padding: 48px 24px;
  text-align: center;
}
.architecture-section .section-title {
  font-size: 2rem;
  font-weight: 800;
  letter-spacing: -0.02em;
  color: var(--vp-c-text-1);
  margin: 0 0 40px 0;
}
.stats-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 24px;
  max-width: 800px;
  margin: 0 auto;
}
.stat-card {
  padding: 24px;
  border-radius: 16px;
  border: 1px solid var(--vp-c-divider);
  background: var(--vp-c-bg-soft);
}
.dark .stat-card {
  background: rgba(255,255,255,0.03);
  border-color: rgba(255,255,255,0.06);
}
.stat-card .number {
  font-size: 2.5rem;
  font-weight: 800;
  color: var(--vp-c-brand-1);
}
.stat-card .label {
  font-size: 0.85rem;
  color: var(--vp-c-text-2);
  margin-top: 4px;
}
@media (max-width: 640px) {
  .stats-grid { grid-template-columns: repeat(2, 1fr); }
}
</style>

<div class="architecture-section">
  <div class="section-title">Built for Security at Scale</div>
  <div class="stats-grid">
    <div class="stat-card">
      <div class="number">15</div>
      <div class="label">LLM Providers</div>
    </div>
    <div class="stat-card">
      <div class="number">5</div>
      <div class="label">Security Rings</div>
    </div>
    <div class="stat-card">
      <div class="number">14</div>
      <div class="label">Marketplace Plugins</div>
    </div>
    <div class="stat-card">
      <div class="number">449</div>
      <div class="label">Tests Passing</div>
    </div>
  </div>
</div>
