# FQDN Risk Scoring — lexical domain reputation

**Module:** `core/fqdn_risk.py` · **Wired into:** `SecurityShield._check_links` (prompts) + `sanitize_response` (responses) · **Default:** disabled (opt-in)

A network-free convergence with [fqdn-model](https://github.com/fabriziosalmi/fqdn-model),
Fabrizio Salmi's malicious-domain classifier.

## Why a lexical subset

fqdn-model is a Random-Forest classifier over ~22 features. Most of its accuracy
comes from **network** features — DNS records, SSL validity, HTTP status, HSTS,
redirect chains, WHOIS, page content. None of those are affordable on the proxy's
synchronous request path, where a link must be scored in **microseconds, not
seconds**, with no outbound I/O.

So this module ports the subset of fqdn-model's `analyze_fqdn` features that are
computable from the **domain string alone**, plus the exact constant lists from
its `settings.py`, and combines them into a transparent 0..1 risk score. It is
**not** the trained Random Forest — it's the offline lexical pre-filter that
augments the static `blocked_domains` deny-list with a "this host looks like a
DGA / typosquat / phishing domain" heuristic. For the full behavioral verdict,
run fqdn-model as a sidecar and wire it in as an opt-in Tier-2 (future work).

## Features (ported from fqdn-model, network-free only)

| Feature | Fires when | Weight |
|---------|-----------|--------|
| `ip_as_host` | host is a bare IPv4 literal | 0.35 |
| `risky_tld` | TLD ∈ `.xyz .top .loan .online .club .click .icu .cn` | 0.30 |
| `shortener` | host ∈ `bit.ly t.co tinyurl.com ow.ly is.gd buff.ly adf.ly` | 0.25 |
| `suspicious_keyword` | host contains `login/verify/secure/bank/account/…` (13 terms) | 0.25 |
| `excess_hyphens` | `> 1` hyphen | 0.20 |
| `excess_digits` | `> 4` digits | 0.20 |
| `excess_subdomains` | `> 3` subdomain labels (www-normalized) | 0.20 |
| `long_domain` | length `> 20` | 0.08 |

The constant lists and thresholds are copied verbatim from fqdn-model's
`settings.py`. The **weights** are this project's own transparent calibration
(not fqdn-model's trained coefficients). The score is the capped sum of the
weights whose feature fired; an IPv4 literal suppresses the noisy
`long_domain`/`excess_digits` features (one signal, not three).

**Combination, not single signals.** A `.xyz` domain alone scores 0.30 — below
the default 0.7 threshold. Blocking requires a *combination* (risky TLD +
phishing keyword + hyphens), which is what keeps false positives low. Benign
hosts (`raw.githubusercontent.com`, `login.microsoftonline.com`, `s3.amazonaws.com`)
stay well under the bar; `secure-login-verify.xyz` clears it at 0.83.

## Configuration

```yaml
security:
  link_sanitization:
    enabled: true
    blocked_domains: [...]        # static deny-list (unchanged)
    risk_scoring:
      enabled: false              # opt-in
      block_threshold: 0.7        # 0..1; lower = stricter (0.5 also catches risky-TLD DGAs)
      log_only: false             # true = log the risk but never block/replace
```

When enabled, a host scoring `>= block_threshold`:
- in a **prompt** → the request is blocked (`_check_links` returns an error), unless `log_only`;
- in a **response** → the URL is replaced with `[BLOCKED_LINK]`, unless `log_only`.

Every hit is logged as `FQDN RISK: <host> score=<s> reasons=[...]`. **Fail-open:**
any scorer error leaves the request/response untouched.

## Tests

`tests/test_fqdn_risk.py` — a benign corpus that must stay below threshold, a
phishing/typosquat corpus that must clear it, per-feature correctness (IPv4
detection, multi-level-TLD subdomain counting, keyword/shortener/TLD matching),
and the SecurityShield wiring on both the prompt and response paths.
