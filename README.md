# LLMProxy — Universal AI Gateway & Autonomous Agentic Mesh

Professional high-performance aggregator, intelligent load balancer, and autonomous discovery engine for Large Language Models. LLMProxy provides a unified, hardened interface for pluralistic AI environments with advanced security, zero-latency observability, and self-healing agent swarms.

## Table of Contents
1. [Architecture Overview](#architecture-overview)
2. [Deep-Dive: Core Components](#deep-dive-core-components)
3. [API Registry & Endpoints](#api-registry--endpoints)
4. [Autonomous Agent Swarm](#autonomous-agent-swarm)
5. [Hardened Security & Governance](#hardened-security--governance)
6. [Plugin Engine & Extensibility](#plugin-engine--extensibility)
7. [Semantic Caching & Optimization](#semantic-caching--optimization)
8. [Frontend & Oversight HUD](#frontend--oversight-hud)
9. [Configuration & Deployment](#configuration--deployment)
10. [Advanced Features & Roadmaps](#advanced-features--roadmaps)

---

## Architecture Overview

LLMProxy is engineered as a multi-tier, distributed system. It separates high-speed request processing (Edge Tier) from background autonomous operations (Agentic Tier), ensuring that system intelligence does not introduce latency into the critical path of inference.

### System Tiers
- **Edge Tier (L7)**: ASGI-based request pipeline, OIDC authentication, and Byte-Level Firewall.
- **Agentic Tier (L2/L3)**: Supervisor-managed swarm for discovery, validation, and self-healing.
- **Persistence Tier**: Multi-modal storage (SQL for metadata, Vector/ChromaDB for semantic patterns).

---

## Deep-Dive: Core Components

### 1. Agent Supervisor
The heart of LLMProxy's background operations. It manages a DAG (Directed Acyclic Graph) of agents, ensuring they are restarted on failure with exponential backoff and localized circuit breaking.

### 2. SOTA Interface Agent (10x Discovery)
Unlike traditional scrapers, this agent uses **Playwright** to:
- **Sniff Network Traffic**: Intercepts actual fetch/XHR calls to discover hidden API endpoints.
- **Genetic Evasion**: Simulates non-linear mouse movements and micro-scroll jitter to bypass advanced WAFs.
- **Pattern Prediction**: Uses `PatternMemory` (Vector Search) to predict the correct API adapter for a new site based on structural similarity to known sites.

### 3. Unified Adapter Engine
Translates standard OpenAI-compatible requests into proprietary provider formats (Anthropic, HuggingFace, Local LLMs) in real-time, handling prompt templating and multi-modal payload mapping.

---

## API Registry & Endpoints

### 1. Model Proxy Interface (Port 8090)
| Endpoint | Method | Context | Description |
|----------|--------|---------|-------------|
| `/v1/chat/completions` | `POST` | OpenAI Compatibility | Unified entry point for chat inference. |
| `/v1/embeddings` | `POST` | Vector Operations | Generates embeddings using the optimal local/remote model. |
| `/health` | `GET` | System Vitality | Returns Liveness and Readiness probes. |
| `/metrics` | `GET` | Observability | Prometheus-formatted export of R/W latencies and error rates. |

### 2. Administrative & Control API (Port 8081)
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/registry` | `GET` | Retrieves the full state of the model pool (Verified vs. Discovered). |
| `/api/v1/registry/{id}/toggle` | `POST` | Instant administrative kill-switch for a specific provider node. |
| `/api/v1/proxy/circuit-breaker` | `GET` | Current status of all internal service circuit breakers. |
| `/api/v1/telemetry/stream` | `GET` | Real-time SSE stream of system security and routing events. |

---

## Autonomous Agent Swarm

The swarm utilizes a Finite State Machine (FSM) for predictable transitions and robust error handling.

| Agent | Module | Primary Intelligence |
|-------|--------|----------------------|
| **SOTA Interface** | `agents/sota_interface_agent.py` | Playwright-based API synthesis and WAF evasion. |
| **Scanner** | `agents/scanner.py` | BFS traversal of web targets to identify potential LLM resources. |
| **Validator** | `agents/validator.py` | Ground-truth verification of model logic and alignment. |
| **Self-Healer** | `agents/self_healer.py` | Auto-remediation of registry drift and service degradation. |
| **Distiller** | `agents/distiller.py` | High-fidelity dataset extraction for SLM (Small Language Model) fine-tuning. |

---

## Hardened Security & Governance

### Byte-Level Firewall (Speculative Guardrails)
A zero-latency ASGI middleware that scans the raw byte stream of incoming/outgoing traffic.
- **Pattern Matching**: Detects malicious signatures (`ignore previous instructions`, `bypass guardrails`) in raw UTF-8.
- **Instant Termination**: Force-closes the socket mid-stream if a violation is detected, preventing remote cost incurrence.

### Zero-Trust (ZT) & Identity
- **OIDC Integration**: Support for Google, Microsoft, and Apple identity providers.
- **RBAC Enforcement**: Fine-grained Role-Based Access Control for models, tools, and admin APIs.
- **mTLS Pipeline**: Cryptographic verification for all upstream model provider connections.

---

## Plugin Engine & Extensibility

LLMProxy features a "Ring-Based" plugin architecture (`core/plugin_engine.py`) for processing requests across 5 specialized rings:

1. **Ring 1: Ingress**: Authentication, Zero-Trust, and Global Rate Limiting.
2. **Ring 2: Pre-Flight**: PII Masking, Prompt Mutation, and AST security scanning.
3. **Ring 3: Routing**: Semantic Caching lookups and Dynamic Model Selection.
4. **Ring 4: Post-Flight**: JSON Healing, Response Sanitization, and Watermarking.
5. **Ring 5: Background**: FinOps tracking, Telemetry export, and Shadow Traffic logging.

*Supports hot-swapping plugins at runtime via the `manifest.yaml` configuration.*

---

## Semantic Caching & Optimization

### 1. 1-Bit Vector Quantization
To achieve ultra-low latency, the `SemanticCache` binarizes embeddings into 1-bit vectors. This renders Cosine Similarity mathematically equivalent to **XOR Hamming Distance**, allowing for CPU-level hardware acceleration of similarity lookups.

### 2. Deterministic Bloom Filter
A front-end Bloom Filter (`bloom.bin`) prevents expensive vector DB lookups for "guaranteed misses," ensuring O(1) latency for unique, never-before-seen queries.

---

## Frontend & Oversight HUD

The Dashboard is a tailored React-based HUD (`frontend/`) for high-stakes operational monitoring.

### Key HUD Views
- **System Executive Dashboard**: Global view of node health, uptime, and throughput.
- **Model Registry HUD**: Real-time visualization of the autonomous discovery process.
- **Live Trace Stream**: Integrated terminal capturing SSE telemetry (blue for requests, red for security violations).
- **Control Center**: Dynamic configuration of Guardrails, Virtual Keys, and Plugin states.

---

## Configuration & Deployment

### Advanced `config.yaml`
```yaml
server:
  host: 0.0.0.0
  port: 8090
  tls: { enabled: true, min_version: "1.2" }
  auth: { enabled: true, api_keys_env: "LLM_PROXY_API_KEYS" }

rotation:
  strategy: "round_robin" # options: weighted, least_used, random
  failover: { enabled: true, max_retries: 3 }

plugins:
  manifest: "/plugins/manifest.yaml"
  hot_swap: true
```

### Installation
```bash
# Core installation
git clone https://github.com/fabriziosalmi/llmproxy
pip install -r requirements.txt

# Playwright setup for SOTA Explorer
playwright install chromium

# Execution
python main.py
```

---

## Advanced Features & Roadmaps

### Federated Mesh (Tailscale)
Leverages Tailscale mesh networks to discover and shard requests across idle GPU neighbors, creating a private, federated "AI Supercluster" without exposing endpoints to the public internet.

### MCP (Model Context Protocol) Hub
A unified execution environment for MCP tools, allowing any connected model (OpenAI, Anthropic, Local) to leverage a shared pool of local tools (PostgreSQL, Filesystem, Search) via a standardized protocol.

### Visibility & Cloaking
Automatic User-Agent rotation and proxy-chaining to ensure that upstream providers cannot easily fingerprint the proxy's infrastructure.
