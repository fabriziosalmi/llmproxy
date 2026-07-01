# SIEM export

LLMProxy emits its security events (injection blocks, auth failures, kill-switch,
budget/circuit events) to your SOC in the two formats every SIEM already ingests.

## ECS (Elastic Common Schema) — over HTTP

Point a webhook at your collector (Splunk HEC, Datadog, Elastic, or any HTTP JSON
sink) and set the target to `siem`. Events are POSTed as ECS JSON, so
`event.category`, `event.action`, `source.ip`, and `user.name` line up with the
fields your dashboards and correlation rules already use.

```yaml
webhooks:
  enabled: true
  endpoints:
    - name: splunk-hec
      target: siem                 # ← Elastic Common Schema JSON
      url_env: SPLUNK_HEC_URL      # secret pulled from env, not stored in config
      events: [injection_blocked, auth_failure, panic_activated, budget_threshold]
```

Example event:

```json
{
  "@timestamp": "2026-07-01T10:00:00+00:00",
  "event": { "kind": "alert", "category": ["intrusion_detection"],
             "type": ["denied"], "action": "injection_blocked", "severity": 8,
             "provider": "llmproxy" },
  "observer": { "vendor": "llmproxy", "product": "llmproxy", "type": "proxy",
                "version": "1.24.1" },
  "source": { "ip": "203.0.113.9" },
  "message": "Prompt injection blocked",
  "llmproxy": { "ip": "203.0.113.9", "reason": "multilingual-override" }
}
```

Outbound webhook URLs are SSRF-validated (private/reserved ranges rejected at load
and at resolve time) — see `core/webhooks.py`.

## CEF (ArcSight Common Event Format) — for syslog SIEMs

For QRadar / ArcSight / on-prem syslog collectors, `core.siem.to_cef()` produces a
standards-compliant `CEF:0|…` line with spec-correct escaping (a crafted event
field cannot forge a second field or break the parser). Wire it to your syslog
transport of choice:

```
CEF:0|llmproxy|llmproxy|1.24.1|injection_blocked|Prompt injection blocked|8|src=203.0.113.9 reason=multilingual-override
```

Both formatters are pure functions with full test coverage in `tests/test_siem.py`.
