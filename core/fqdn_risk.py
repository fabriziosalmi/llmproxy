"""
Offline lexical FQDN risk scorer — a network-free convergence with fqdn-model
(github.com/fabriziosalmi/fqdn-model, MIT).

fqdn-model is a Random-Forest domain classifier over ~22 behavioral features.
Most of its accuracy comes from *network* features (DNS records, SSL validity,
HTTP status, HSTS, redirect chains, WHOIS, page content) — none of which are
affordable on the proxy's synchronous request path, where a single link must be
scored in microseconds, not seconds.

This module ports the subset of fqdn-model's features that are computable from
the **domain string alone** (its `analyze_fqdn` network-free features) and the
exact constant lists from its `settings.py`, then combines them into a
transparent, weighted 0..1 risk score. It is intentionally NOT the trained
Random Forest: it is the offline lexical pre-filter that augments the static
`blocked_domains` allow/deny list with a "this looks like a DGA / typosquat /
phishing host" heuristic. For the full behavioral verdict, run fqdn-model as a
sidecar service and wire it in as an opt-in Tier-2 (a future enhancement).

Design goals: zero dependencies, deterministic, and calibrated so ordinary
domains (google.com, raw.githubusercontent.com, login.microsoftonline.com) score
well below the default block threshold while risky-TLD + phishing-keyword +
obfuscation combinations clear it. Weak single signals never block on their own —
risk requires a *combination*, which is what keeps false positives low.
"""
from __future__ import annotations

import re
from typing import List, Tuple

# ── Constants ported verbatim from fqdn-model/settings.py ─────────────────────
RISKY_TLDS = frozenset(
    (".xyz", ".top", ".loan", ".online", ".club", ".click", ".icu", ".cn")
)
SHORTENER_DOMAINS = frozenset(
    ("bit.ly", "t.co", "tinyurl.com", "ow.ly", "is.gd", "buff.ly", "adf.ly")
)
SUSPICIOUS_KEYWORDS = (
    "login", "signin", "account", "verify", "secure", "update", "bank",
    "payment", "free", "download", "admin", "password", "credential",
)
GOOD_DOMAIN_LENGTH = 20  # fqdn-model: len <= this is unremarkable
MAX_HYPHENS = 1
MAX_DIGITS = 4
MAX_SUBDOMAINS = 3

# Transparent scoring weights (this project's calibration, not fqdn-model's
# trained coefficients). A domain's score is the capped sum of the weights whose
# feature fired. Tuned so benign hosts stay < DEFAULT_BLOCK_THRESHOLD and a
# realistic phishing/DGA host (risky TLD + keyword + hyphens) clears it.
WEIGHTS = {
    "ip_as_host": 0.35,
    "risky_tld": 0.30,
    "shortener": 0.25,
    "suspicious_keyword": 0.25,
    "excess_hyphens": 0.20,
    "excess_digits": 0.20,
    "excess_subdomains": 0.20,
    "long_domain": 0.08,
}
DEFAULT_BLOCK_THRESHOLD = 0.7

# Common multi-label public suffixes, so subdomain counting doesn't over-count
# a two-level TLD (foo.co.uk has 0 subdomains, not 1). Not the full PSL — a
# pragmatic set covering the frequent cases; the feature weight is small anyway.
_MULTI_LEVEL_TLDS = frozenset(
    (
        "co.uk", "org.uk", "gov.uk", "ac.uk", "co.jp", "co.kr", "com.au",
        "net.au", "org.au", "com.br", "com.cn", "com.mx", "co.in", "co.za",
        "co.nz", "com.tr", "com.sg", "com.hk",
    )
)

_IPV4_RE = re.compile(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$")


def _is_ipv4(host: str) -> bool:
    m = _IPV4_RE.match(host)
    if not m:
        return False
    return all(0 <= int(g) <= 255 for g in m.groups())


def _subdomain_count(host: str) -> int:
    """Approximate the number of subdomain labels beyond the registrable domain,
    excluding a leading 'www'. Uses a small multi-level-TLD table instead of the
    full Public Suffix List (the feature weight is small and needs >3 to fire)."""
    labels = host.split(".")
    if len(labels) < 3:
        return 0
    # eTLD+1 size: 3 labels for a known two-level TLD, else 2.
    last_two = ".".join(labels[-2:])
    etld1_size = 3 if last_two in _MULTI_LEVEL_TLDS else 2
    subs = labels[:-etld1_size]
    if subs and subs[0] == "www":
        subs = subs[1:]
    return len(subs)


def features(host: str) -> List[str]:
    """Return the list of network-free risk features that fire for `host`.
    `host` should already be a bare hostname (no scheme/port/path)."""
    host = host.strip().lower().rstrip(".")
    if not host:
        return []
    fired: List[str] = []

    if _is_ipv4(host):
        fired.append("ip_as_host")

    if any(host.endswith(tld) for tld in RISKY_TLDS):
        fired.append("risky_tld")

    if host in SHORTENER_DOMAINS:
        fired.append("shortener")

    if any(kw in host for kw in SUSPICIOUS_KEYWORDS):
        fired.append("suspicious_keyword")

    if host.count("-") > MAX_HYPHENS:
        fired.append("excess_hyphens")

    if sum(c.isdigit() for c in host) > MAX_DIGITS:
        fired.append("excess_digits")

    if _subdomain_count(host) > MAX_SUBDOMAINS:
        fired.append("excess_subdomains")

    if len(host) > GOOD_DOMAIN_LENGTH:
        fired.append("long_domain")

    return fired


def assess(host: str) -> Tuple[float, List[str]]:
    """Score a hostname. Returns (risk 0..1, fired-feature tags).

    Deterministic and total: any input is scored, an empty/garbage host → 0.0.
    IP-as-host suppresses the noisy `long_domain`/`excess_digits` lexical
    features (an IPv4 literal is a single distinct signal, not three)."""
    fired = features(host)
    if "ip_as_host" in fired:
        fired = [f for f in fired if f not in ("long_domain", "excess_digits")]
    score = min(1.0, sum(WEIGHTS.get(f, 0.0) for f in fired))
    return score, fired
