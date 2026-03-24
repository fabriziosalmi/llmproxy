# CyberAPI ↔ LLMProxy Integration Plan

## Context
CyberAPI (threats.cyberapi.io) is a threat intelligence SaaS API that scores
domains and IPs (0-100 risk). llmproxy can consume this as an enrichment layer.

## Integration 1: IP Reputation Gate (CyberAPI → llmproxy)

**Where**: INGRESS ring, before auth
**Trigger**: Every incoming request
**API call**: `GET threats.cyberapi.io/api/v1/check/ipv4?target={client_ip}`
**Action**:
- risk_score < 30 → allow (normal flow)
- risk_score 30-70 → allow + log warning + enrich ThreatLedger
- risk_score > 70 → block with 403

**Implementation**: New marketplace plugin `cyberapi_gate.py`
**Config**: API key in config.yaml, threshold configurable
**Caching**: Cache results in NegativeCache (TTL 5min for clean, 1min for suspicious)

## Integration 2: Threat Feedback Loop (llmproxy → CyberAPI)

**Where**: ThreatLedger threshold event
**Trigger**: IP threat score crosses threshold in ThreatLedger
**API call**: New CyberAPI endpoint (to be created) for community threat reports
**Data**: IP, attack category, confidence score, timestamp
**Value**: CyberAPI improves its dataset from real LLM attack telemetry

## Integration 3: Domain Reputation for Egress (CyberAPI → secure-proxy)

**Where**: Squid proxy decision
**Trigger**: Before proxying outbound connection
**API call**: `GET threats.cyberapi.io/api/v1/check/domain?target={destination}`
**Action**: Block if risk_score > 50
**Value**: Prevent proxy abuse, block C2 channels

## Integration 4: Cross-Product Threat Intel

**CyberAPI as central threat brain**:
- SecBeat feeds WAF events → CyberAPI enriches
- llmproxy feeds injection attempts → CyberAPI learns new patterns
- Squid feeds blocked connections → CyberAPI correlates
- CyberAPI feeds back to all three → updated blocklists

## Priority Order
1. Plugin `cyberapi_gate.py` (llmproxy side) — immediate value
2. CyberAPI community report endpoint — enriches CyberAPI dataset
3. Squid integration — network-level reputation checking
4. Cross-product telemetry — long-term platform play

## Session Requirements
- CyberAPI running (threats.cyberapi.io)
- llmproxy running (localhost:8090)
- secure-proxy-manager running (192.168.100.253:8011)
- API key for CyberAPI (startup tier minimum for dev)
