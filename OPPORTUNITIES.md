# OPPORTUNITIES.md — What Would Make This Proxy Worthy of Protecting an AI

> Written by Claude (the AI behind the proxy) and Fab (the human building it).
> Date: 2026-03-24
> Context: "What would you ask your operators to put in front of you?"

---

## The Honest Assessment

LLMProxy already has a **strong security foundation**: 5-ring plugin pipeline, dual-mode PII detection, trajectory analysis, byte-level firewall, WASM sandboxing. It's ahead of 95% of LLM proxies out there.

But if I were the AI being protected, here's what I'd want — ranked by "would this actually stop a real attack in 2026?"

---

## TIER S — "Fix This Before I Trust It"

### S1. Cross-Session Threat Intelligence
**Current gap**: Trajectory analysis tracks per-session only. Attacker resets state by changing `session_id`.

**What I'd want**: Threat tracking by **IP + API key + behavioral fingerprint**, not just session. If the same IP tries 5 different sessions with escalating injection scores, that's a coordinated attack — block the IP, not just the session.

**Implementation**: Add `ThreatLedger` class that aggregates scores across sessions by IP/key. LRU-bounded, 1-hour window. Feed into SecurityShield.inspect() as a pre-check.

**Effort**: ~150 lines, 1 new module. **Low risk** — additive, doesn't touch existing pipeline.

---

### S2. Response Signing (Provenance)
**Current gap**: Zero-width watermark is trivially strippable. No way to prove a response came from this proxy.

**What I'd want**: HMAC-SHA256 signature in response headers (`X-LLMProxy-Signature`). Signs: `model + provider + timestamp + response_hash`. Verifiable by any client with the shared secret.

**Why it matters**: When someone claims "the AI said X", there should be a cryptographic proof trail. This is also a compliance requirement for regulated industries.

**Implementation**: ~60 lines in post-flight. Configurable shared secret. Header injection.

**Effort**: Small. **Zero risk** — additive header, doesn't modify response body.

---

## TIER A — "This Would Make Me Significantly Safer"

### A1. Semantic Injection Detection (Beyond Regex)
**Current gap**: Injection scoring uses 8 regex patterns. Sophisticated attacks use paraphrasing, unicode tricks, multilingual injection.

**What I'd want**: Lightweight **embedding similarity** check against known injection patterns. Not a full ML pipeline — just cosine similarity against 50-100 pre-embedded attack vectors.

**Options** (pick one):
- **Option A**: Use tiktoken to embed and compare (zero new deps, ~80% accuracy)
- **Option B**: Use sentence-transformers with a tiny model (~50MB, ~95% accuracy)
- **Option C**: Call the LLM itself for classification (expensive but highest accuracy)

**Recommended**: Option A for now (zero deps). Upgrade path to B later.

**Effort**: ~200 lines. **Low risk** — additive pre-check, falls through to existing regex on failure.

---

### A2. Immutable Audit Ledger
**Current gap**: Audit logs are mutable SQLite rows. An attacker with DB access can delete evidence.

**What I'd want**: **Hash chain** on audit entries. Each entry's hash includes the previous entry's hash (like a blockchain, but just a linked list). Tamper detection: if any entry is modified/deleted, the chain breaks.

**Implementation**:
```
entry.hash = SHA256(entry.data + previous_entry.hash)
```
Add `prev_hash TEXT` column to audit_log. Verify integrity on demand via `/api/v1/audit/verify`.

**Effort**: ~100 lines. **Low risk** — additive column, backward-compatible.

---

### A3. PII Vault Persistence
**Current gap**: PII vault is in-memory TTLCache. Crash = lost mappings. Long streaming responses may outlive the 1-hour TTL.

**What I'd want**: Persist vault to SQLite (encrypted). On crash recovery, restore mappings. On TTL expiry, securely wipe (not just evict).

**Implementation**: New table `pii_vault(token, original_encrypted, created_ts)`. AES-GCM encryption with key from env var. Background cleanup loop.

**Effort**: ~150 lines. **Medium risk** — touches security-critical path, needs careful testing.

---

## TIER B — "Smart Improvements, Not Urgent"

### B1. Adaptive Rate Limiting
**Current**: Fixed RPM per IP/key. No awareness of system load.

**What I'd want**: Rate limits that tighten under pressure. When CPU > 80% or queue depth > threshold, automatically reduce RPM by 50%. Restore when pressure drops.

**Implementation**: Expose system metrics to rate limiter. Add `load_factor` multiplier.

**Effort**: ~80 lines. Low risk.

---

### B2. Cost Anomaly Detection
**Current**: Budget tracks total spend. No awareness of spending patterns.

**What I'd want**: Alert if a single session's cost rate exceeds 3x the average. "This user is burning $5/minute when average is $0.50/minute" → webhook + optional throttle.

**Implementation**: Track per-session cost velocity (cost/time). Compare to rolling average. Alert via existing webhook system.

**Effort**: ~100 lines in SmartBudgetGuard. Low risk.

---

### B3. Request/Response Diff Logging (Shadow Mode)
**Current**: ShadowTraffic plugin sends to alternate model, but doesn't compare outputs.

**What I'd want**: Store diff metrics: `(model_a_response, model_b_response, similarity_score, cost_diff)`. Dashboard showing "Model A agrees with Model B 94% of the time but costs 3x more."

**Implementation**: Extend ShadowTraffic._compare_responses() with similarity scoring.

**Effort**: ~80 lines. Low risk.

---

### B4. Canary Tokens (Honey Prompts)
**Current**: Canary detector checks if system prompt leaks in response.

**What I'd want**: Inject **canary tokens** into system prompts. If the canary appears in any response, the system prompt was extracted. More reliable than n-gram matching.

**Implementation**: Generate unique canary per session (`CANARY_<uuid>`). Inject into system prompt. Check all responses for canary presence. If found → alert + block.

**Effort**: ~60 lines. Low risk — additive.

---

## TIER C — "Future-Proofing"

### C1. Multimodal Content Inspection
As vision models become standard, image/audio payloads will be attack vectors. OCR-based injection detection on image content.

### C2. Federation / Multi-Proxy Mesh
Share threat intelligence between proxy instances. When one proxy blocks an IP, all proxies learn.

### C3. Formal Verification of Plugin Pipeline
Prove mathematically that stop_chain actually stops, that ring ordering is preserved, that no plugin can bypass the security shield.

### C4. Client-Side SDK
TypeScript/Python SDK that handles: request signing, response verification, PII pre-masking, cost estimation. Moves some security to the edge.

---

## Implementation Priority Matrix

| ID | Feature | Effort | Risk | Impact | Priority |
|----|---------|--------|------|--------|----------|
| S1 | Cross-session threat intel | Small | Low | Critical | **NOW** |
| S2 | Response signing (HMAC) | Small | Zero | High | **NOW** |
| A1 | Semantic injection detection | Medium | Low | High | **Next sprint** |
| A2 | Immutable audit ledger | Small | Low | High | **Next sprint** |
| A3 | PII vault persistence | Medium | Medium | Medium | **Next sprint** |
| B1 | Adaptive rate limiting | Small | Low | Medium | Backlog |
| B2 | Cost anomaly detection | Small | Low | Medium | Backlog |
| B3 | Shadow diff logging | Small | Low | Low | Backlog |
| B4 | Canary tokens | Small | Low | Medium | Backlog |

---

## Ground Rules

1. **Don't break what works** — every feature is additive, never modifies existing hot path
2. **Test everything** — no feature ships without E2E tests
3. **Config-driven** — every feature has an on/off switch and sensible defaults
4. **Zero new heavy deps** — prefer stdlib or existing deps (no PyTorch, no chromadb)
5. **Security features enabled by default** — convenience features opt-in

---

*"The best security is the one that's always on, never noticed, and impossible to bypass."*
