# Injection Scoring & Trajectory Detection

The **SecurityShield** (`core/security.py`) provides deep inspection for prompt injection attempts, running pre-INGRESS in the request chain.

## Injection Scoring

SecurityShield uses regex-based threat scoring to evaluate each request. The score represents the likelihood of a prompt injection attempt.

Requests exceeding the configurable threshold are blocked with a 403 response including diagnostic information:

```json
{
  "error": "Request blocked by SecurityShield",
  "type": "injection_detected",
  "score": 0.85,
  "threshold": 0.7
}
```

## Multi-Turn Trajectory Detection

SecurityShield tracks prompt injection scores **per session** over time. This detects a common attack pattern: gradually escalating jailbreak attempts across multiple turns.

A sliding window analysis flags sessions where the injection score trend is increasing, even if individual requests stay below the threshold.

```
Turn 1: score 0.2 → pass
Turn 2: score 0.35 → pass
Turn 3: score 0.5 → pass
Turn 4: score 0.6 → TRAJECTORY ALERT (escalating pattern)
```

## Pipeline Position

SecurityShield runs in `RotatorAgent.proxy_request()` **before** the plugin INGRESS ring — it is the first code-level inspection after the ASGI firewall.

```
ASGI Firewall → SecurityShield.inspect() → Plugin INGRESS ring → ...
```

## Complement: Topic Blocklist

For content-level blocking (rather than injection detection), use the [Topic Blocklist](/plugins/marketplace#topic-blocklist) marketplace plugin which supports keyword, whole-word, and regex matching.
