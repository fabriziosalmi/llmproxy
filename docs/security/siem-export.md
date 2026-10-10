# SIEM Export

LLMProxy can send its security events (injection blocks, auth failures, kill switch,
budget and circuit events) to a collector as ECS JSON over HTTP.

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

## CEF

`core.siem.to_cef()` formats an event as a `CEF:0|...` line with field escaping, for
QRadar, ArcSight or a syslog collector. **Nothing in the proxy calls it**: there is no
syslog transport and no webhook target that emits CEF. It is a formatter you can use
from your own code, not an export the proxy performs.

The audit chain's hourly `AUDIT HEAD` line goes to the process log, not to this export.
