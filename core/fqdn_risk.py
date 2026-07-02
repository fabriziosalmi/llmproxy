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

import ipaddress
from typing import List, Optional, Tuple

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
    "private_ip": 0.45,   # loopback/private/link-local/reserved IP literal in a link
    "ip_as_host": 0.35,   # any IP literal used as the host (obfuscation)
    "risky_tld": 0.30,
    "shortener": 0.25,
    "punycode": 0.25,     # xn-- IDN — classic homograph phishing vector
    "excess_hyphens": 0.20,
    "excess_digits": 0.20,
    "excess_subdomains": 0.20,
    "long_domain": 0.08,
}
# Phishing keywords score PER DISTINCT HIT (capped), so a host stuffed with
# login/verify/account/bank/… escalates rather than counting once. Three hits
# (0.60) + one more signal clears the threshold — which is what a realistic
# `paypal-login-verify-account.com` looks like.
KEYWORD_HIT_WEIGHT = 0.20
KEYWORD_HIT_CAP = 3
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

def _parse_ip(host: str) -> "Optional[ipaddress.IPv4Address | ipaddress.IPv6Address]":
    """Return an ipaddress object if `host` is ANY IP literal, else None.

    Covers the forms attackers use to obfuscate a loopback/internal target in a
    link: dotted IPv4 (127.0.0.1), IPv6 (bracketed or bare, ::1), and integer
    literals — decimal (2130706433) and hex (0x7f000001). The old dotted-quad
    regex matched only the first, so the others sailed through as normal hosts.
    """
    h = host.strip().strip("[]")
    try:
        return ipaddress.ip_address(h)
    except ValueError:
        pass
    try:
        if h.lower().startswith("0x"):
            n = int(h, 16)
        elif h.isdigit():
            n = int(h)
        else:
            return None
        if 0 <= n <= 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF:
            return ipaddress.ip_address(n)
    except (ValueError, ipaddress.AddressValueError):
        pass
    return None


def _keyword_hits(host: str) -> int:
    return sum(1 for kw in SUSPICIOUS_KEYWORDS if kw in host)


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


def assess(host: str) -> Tuple[float, List[str]]:
    """Score a hostname. Returns (risk 0..1, fired-feature reason tags).

    Deterministic and total: any input is scored; an empty/garbage host → 0.0.
    An IP literal is scored on its own (ip_as_host, plus private_ip for
    loopback/internal targets) and short-circuits the lexical features — a bare
    address has no meaningful length/digit/hyphen signal."""
    host = host.strip().lower().rstrip(".")
    reasons: List[str] = []
    if not host:
        return 0.0, reasons
    score = 0.0

    ip = _parse_ip(host)
    if ip is not None:
        reasons.append("ip_as_host")
        score += WEIGHTS["ip_as_host"]
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            reasons.append("private_ip")
            score += WEIGHTS["private_ip"]
        return min(1.0, score), reasons

    if any(host.endswith(tld) for tld in RISKY_TLDS):
        reasons.append("risky_tld")
        score += WEIGHTS["risky_tld"]

    if host in SHORTENER_DOMAINS:
        reasons.append("shortener")
        score += WEIGHTS["shortener"]

    hits = _keyword_hits(host)
    if hits:
        reasons.append("suspicious_keyword")
        score += min(hits, KEYWORD_HIT_CAP) * KEYWORD_HIT_WEIGHT

    if "xn--" in host:
        reasons.append("punycode")
        score += WEIGHTS["punycode"]

    if host.count("-") > MAX_HYPHENS:
        reasons.append("excess_hyphens")
        score += WEIGHTS["excess_hyphens"]

    if sum(c.isdigit() for c in host) > MAX_DIGITS:
        reasons.append("excess_digits")
        score += WEIGHTS["excess_digits"]

    if _subdomain_count(host) > MAX_SUBDOMAINS:
        reasons.append("excess_subdomains")
        score += WEIGHTS["excess_subdomains"]

    if len(host) > GOOD_DOMAIN_LENGTH:
        reasons.append("long_domain")
        score += WEIGHTS["long_domain"]

    return min(1.0, score), reasons


def features(host: str) -> List[str]:
    """The list of network-free risk feature tags that fire for `host`
    (no scheme/port/path). Kept as a thin wrapper over assess() for callers
    that only want the reasons."""
    return assess(host)[1]
