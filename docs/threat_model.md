# LLMProxy Formal Threat Model

This document outlines the formal threat modeling analysis for LLMProxy using the STRIDE methodology. It systematically identifies potential threats, their impact, and the mitigating controls currently implemented or planned.

## Scope
The scope includes the 5-Ring Security Pipeline, the ASGI Middleware (Rate Limiting, Circuit Breakers), the proxy endpoints (`/v1/chat/completions`), the Admin UI endpoints (`/api/v1/`), and internal state stores (Redis, SQLite).

---

## 1. Spoofing Identity (Authentication Bypass)
**Threat:** An attacker successfully poses as a legitimate client or admin to access restricted models or administrative dashboards.
* **Vector 1: Admin UI Spoofing:** Extracting or brute-forcing the static API keys for `/api/v1/*`.
* **Vector 2: Upstream Spoofing:** Forging JWT/OIDC tokens if signature validation is weak.
* **Mitigation:**
  - Transitioning Admin UI from static API keys to full OpenID Connect (OIDC) / JWT validation with RS256 signatures.
  - Strict Bearer token validation for upstream requests via `authlib` integration.

## 2. Tampering with Data
**Threat:** Malicious modification of request payloads, telemetry data, or internal configuration states.
* **Vector 1: Prompt Injection:** Modifying the system instructions via adversarial prompts (OWASP LLM01).
* **Vector 2: Ledger Tampering:** Corrupting the Threat Ledger state or SQLite databases.
* **Mitigation:**
  - **Prompt Injection:** Addressed via the 5-ring inspection pipeline (Regex heuristics + AST scanning). *Note: Recognized as fundamentally fragile against probabilistic models. Planned migration to semantic vector analysis.*
  - **State Integrity:** SQLite migrated to Redis for volatile state (rate limits/circuit breakers) to prevent race-condition tampering. Audit logs are appending to WORM-compliant storage.

## 3. Repudiation
**Threat:** An attacker performs a malicious action (e.g., draining the API budget) and the system lacks the audit trail to prove they did it.
* **Vector:** Lack of cryptographic non-repudiation on forwarded payloads.
* **Mitigation:**
  - `EventLogger` implements strict audit trails.
  - S2 Cryptographic Provenance (`ResponseSigner`) signs all outgoing responses to prove the data flowed through the proxy and was not tampered with post-proxy.

## 4. Information Disclosure
**Threat:** Leakage of sensitive data (PII, API Keys, system architecture) to unauthorized parties.
* **Vector 1: PII Leakage:** The LLM regurgitates sensitive data.
* **Vector 2: Secret Leakage:** The proxy logs upstream API keys or leaks them in 500 stack traces.
* **Mitigation:**
  - Strict filtering of `Authorization` headers from all logs.
  - L4 PII Scanner (Presidio) sanitizes outgoing prompts and incoming responses.
  - FastAPI exception handlers trap 500 errors to prevent stack trace leakage.

## 5. Denial of Service (DoS)
**Threat:** Exhausting system resources to make the proxy or upstream LLMs unavailable.
* **Vector 1: Application-Layer Flooding:** Overwhelming the pipeline with massive payloads that exhaust AST parsers or regex engines.
* **Vector 2: Budget Exhaustion:** "Wallet Exhaustion" attacks draining the FinOps budget.
* **Mitigation:**
  - **Distributed Rate Limiting:** Lua-backed Redis token buckets enforce strict per-IP and per-key rate limits at the ASGI middleware level, dropping traffic before it hits the expensive Python pipeline.
  - **FinOps Routing:** Real-time budget tracking halts upstream requests when thresholds are met.
  - **Payload Caps:** Hard limits on max tokens and request body sizes.

## 6. Elevation of Privilege
**Threat:** An unprivileged user gains administrative access or executes arbitrary code on the proxy host.
* **Vector 1: RCE in Plugins:** Exploiting the Python AST evaluator or dynamic plugin loader.
* **Vector 2: Path Traversal:** Accessing internal configuration files via vulnerable admin endpoints.
* **Mitigation:**
  - Zero Trust Manager enforces strict RBAC.
  - **Architectural Debt:** The Python AST evaluator is recognized as a high-risk component. Mitigation roadmap includes migrating untrusted plugins to isolated WebAssembly (Wasm) runtimes.

---

## Residual Risks & Action Plan
- **Deterministic Evasion:** The current regex-based WAF is recognized as "security theater" against advanced prompt injections.
- **Action:** Transition to lightweight ML classification models (e.g., local ONNX models) in the pre-flight pipeline.
