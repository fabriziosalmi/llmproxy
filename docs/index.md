---
layout: home

hero:
  name: "LLMProxy"
  text: "A self-hosted LLM gateway with an audit log you can verify"
  tagline: OpenAI-compatible. Records every request it handles in a hash chain, applies policy to what a response may do, and runs as one process on your infrastructure.
  image:
    src: /logo.svg
    alt: LLMProxy logo
  actions:
    - theme: brand
      text: Quick start
      link: /guide/quickstart
    - theme: alt
      text: What the audit log proves
      link: /threat_model
    - theme: alt
      text: GitHub
      link: https://github.com/fabriziosalmi/llmproxy

features:
  - title: Verifiable audit log
    details: Served, refused and failed requests are rows of a hash chain. The chain can be keyed with HMAC, its head recorded outside the database, and retention and erasure leave a record in the chain.
    link: /api/admin#audit-integrity
  - title: Tool policy
    details: Which tools a response may call, and which may follow a tool result. It stops the call an injected instruction asks for without having to recognise the instruction.
    link: /security/tool-policy
  - title: Measured detection
    details: The firewall and the shield are lexical. What they stop on public datasets is measured, published and regenerated from a results file, misses included.
    link: /security/benchmark
  - title: Provider translation and fallback
    details: Adapters for OpenAI, Anthropic, Google, Azure OpenAI and Ollama, plus OpenAI-compatible providers. Fallback chains, a circuit breaker per endpoint, a daily budget limit.
    link: /guide/configuration
  - title: PII masking
    details: Regular expressions by default, Presidio when installed. Masked before the request leaves, restored in the response.
    link: /security/pii-detection
  - title: One process, your infrastructure
    details: SQLite by default, Postgres optional. No hosted component and no telemetry. Not built to run as several replicas.
    link: /guide/deployment
---
