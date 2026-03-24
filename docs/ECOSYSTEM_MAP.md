# Fab's Security Ecosystem — Complete Map

## The Stack

| # | Project | Role | Tech | Status |
|---|---------|------|------|--------|
| 1 | **SecBeat/edge99** | Edge ingress (WAF, DDoS, TLS, HTTP/3) | Rust, eBPF, K3s | Production (5 POP) |
| 2 | **llmproxy** | LLM security gateway | Python, FastAPI | v1.7.1 (687 tests) |
| 3 | **secure-proxy-manager** | Network egress filter | Squid, Python | Production |
| 4 | **CyberAPI** | Threat intelligence SaaS | FastAPI, Redis | Production (revenue) |
| 5 | **Wildbox** | SOC/SIEM command center | Next.js, FastAPI, 10 services | v0.5.5 |
| 6 | **TLS fingerprinter** | JA3/JA4 bot detection | — | Library |
| 7 | **ASN API** | Network intelligence | — | Library |
| 8 | **HPWM** | Offensive security research (WAF/CAPTCHA bypass) | Python (PyTorch), Rust (WASM) | HackerOne disclosed |

## Architecture

```
                        INTERNET
                           │
             ┌─────────────▼──────────────┐
             │   L1: SecBeat (Rust)        │  EDGE INGRESS
             │   WAF · eBPF · JA3 · HTTP/3 │
             └─────────────┬──────────────┘
                           │
             ┌─────────────▼──────────────┐
             │   L3: llmproxy (Python)     │  LLM APPLICATION
             │   Injection · PII · Cost    │
             └─────────────┬──────────────┘
                           │ outbound via ↓
             ┌─────────────▼──────────────┐
             │   L2: Squid (Egress)        │  NETWORK EGRESS
             │   Whitelist · IP block      │
             └─────────────┬──────────────┘
                           │
             ┌─────────────▼──────────────┐
             │   LLM Providers             │
             └─────────────────────────────┘

        ┌──────────────────────────────────────┐
        │          CONTROL PLANE                │
        │                                      │
        │  CyberAPI ← Threat Intel (SaaS)      │
        │  Wildbox  ← SOC/SIEM (57 tools,      │
        │              SOAR, CSPM, Data Lake)   │
        │                                      │
        │  Wildbox orchestrates all layers      │
        │  via SOAR playbooks + n8n workflows   │
        └──────────────────────────────────────┘
```

## Integration Points (All APIs Exist)

| From → To | API | Data |
|-----------|-----|------|
| CyberAPI → llmproxy | `/api/v1/check/ipv4` | IP risk score for auth gate |
| CyberAPI → SecBeat | `/api/v1/check/domain` | Domain blocklist enrichment |
| CyberAPI → Squid | `/api/v1/check/domain` | Egress domain validation |
| llmproxy → SecBeat | `/_cluster/blacklist` | Ban IP at eBPF level |
| llmproxy → Squid | `/api/ip-blacklist` | Block exfil destination |
| SecBeat → CyberAPI | Webhook | WAF events for TI enrichment |
| Wildbox → All | SOAR playbooks | Orchestrated incident response |
| Wildbox → llmproxy | LLM agent routing | Threat analysis via secured LLM |
| Wildbox → CyberAPI | Data Lake source | 50+ feeds + CyberAPI intel |

## What No Competitor Has

| Capability | Our Stack | Competitors |
|---|:---:|:---:|
| eBPF kernel DDoS filtering | ✅ SecBeat | ❌ |
| 166 OWASP WAF rules (Rust) | ✅ SecBeat | Partial (Cloudflare) |
| LLM-specific injection detection | ✅ llmproxy | ❌ |
| Cross-session threat intelligence | ✅ llmproxy | ❌ |
| Network egress whitelist | ✅ Squid | ❌ |
| Supply chain .pth detection | ✅ llmproxy | ❌ |
| Threat intel API (SaaS) | ✅ CyberAPI | Competitors ($$$) |
| 57 unified security tools | ✅ Wildbox | Partial (Splunk $$$) |
| SOAR with YAML playbooks | ✅ Wildbox | Partial (Demisto $$$) |
| Multi-cloud CSPM 120+ controls | ✅ Wildbox | Competitors ($$$) |
| Full self-hosted, MIT licensed | ✅ All | ❌ |
| **All integrated, one operator** | ✅ | ❌ |

## Offense ↔ Defense Feedback Loop

HPWM (offensive research) directly informs defensive improvements:

| HPWM Finding | Defensive Response |
|---|---|
| JA3 fingerprints are spoofable (6 TLS profiles) | SecBeat: multi-signal detection (JA3 + JA4 + HTTP/2 frames) |
| CDP `isTrusted=true` defeats behavioral heuristics | llmproxy: don't rely on client-side trust signals |
| GNN can parse any site topology in <15ms | Wildbox: use same tech for defensive scanning |
| Biomechanical simulation fools timing analysis | SecBeat: need server-side challenge, not just timing |
| Continuous learning adapts to WAF updates | All: defense must also auto-adapt (llmproxy ThreatLedger) |

This dual perspective (builder + breaker) is what makes the stack credible.
Responsible disclosure via HackerOne (#3619496) demonstrates ethical commitment.

## Session Plan: Full Integration

1. Route Wildbox LLM agents through llmproxy
2. Add CyberAPI as Wildbox Data Lake source
3. SOAR playbook: unified threat response across all 5 systems
4. SecBeat as CDN/WAF in front of Wildbox gateway
5. Squid as egress filter for Wildbox tools + CSPM
6. Combined Docker Compose (all services)
7. E2E attack simulation through full stack
