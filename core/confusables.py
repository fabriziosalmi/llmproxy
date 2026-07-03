"""Unicode confusable / homoglyph folding — the single source of truth.

Three call sites used to each carry their own hand-rolled ~15-entry Cyrillic/Greek
map (core/semantic_analyzer.py, core/firewall_asgi.py, and the homoglyph logic in
SecurityShield.sanitize_response). They drifted apart and none was complete. This
module centralises them and expands coverage to the *practically abused*
Latin-confusable scripts.

Scope decision (honest, not marketing): NFKC already folds the two largest
confusable classes — Mathematical Alphanumeric Symbols (𝚋𝚊𝚗𝚔), Fullwidth Latin
(ｂａｎｋ), and the ligatures. What NFKC does NOT fold, and what this table adds, is
the **Cyrillic and Greek** letters that render identically to ASCII Latin. Those
are ~100% of real-world IDN homograph phishing. Armenian/Cherokee/etc. are
deliberately excluded: their inclusion raises the false-positive rate on genuine
non-Latin text far more than it closes any observed attack, and this is NOT the
full UTS #39 confusables table — it is its Latin-target subset for the scripts
that actually get weaponised. For the exhaustive skeleton algorithm, run a UTS #39
implementation as a Tier-2 sidecar.

Every mapping targets ASCII lowercase [a-z]. Letters with no genuine Latin
look-alike (ж, ш, я, Greek ξ, ψ …) are intentionally absent so that genuine
Cyrillic/Greek prose does NOT fully latinise into an accidental signature match.
"""
from __future__ import annotations

import unicodedata

# ── Cyrillic → Latin (the dominant homograph vector) ─────────────────────────
# Lower and upper, only the codepoints with a true visual Latin twin.
_CYRILLIC = {
    # lowercase
    "а": "a",  # а
    "в": "b",  # в (ve — resembles Latin B)
    "е": "e",  # е
    "ѕ": "s",  # ѕ (dze)
    "і": "i",  # і (Ukrainian i)
    "ј": "j",  # ј (je)
    "к": "k",  # к
    "м": "m",  # м (em)
    "н": "h",  # н (en — resembles H)
    "о": "o",  # о
    "р": "p",  # р
    "с": "c",  # с (es)
    "т": "t",  # т (te)
    "у": "y",  # у (u — resembles y)
    "х": "x",  # х (ha)
    "ԁ": "d",  # ԁ (komi de)
    "ԛ": "q",  # ԛ (qa)
    "ԝ": "w",  # ԝ (we)
    # uppercase
    "А": "a",  # А
    "В": "b",  # В
    "Е": "e",  # Е
    "К": "k",  # К
    "М": "m",  # М
    "Н": "h",  # Н
    "О": "o",  # О
    "Р": "p",  # Р
    "С": "c",  # С
    "Т": "t",  # Т
    "У": "y",  # У
    "Х": "x",  # Х
    "Ѕ": "s",  # Ѕ
    "І": "i",  # І
    "Ј": "j",  # Ј
    "һ": "h",  # һ (shha — resembles h)
}

# ── Greek → Latin (only the unambiguous twins) ───────────────────────────────
_GREEK = {
    # lowercase
    "α": "a",  # α
    "β": "b",  # β
    "ε": "e",  # ε
    "ι": "i",  # ι
    "κ": "k",  # κ
    "ν": "v",  # ν (nu — resembles v)
    "ο": "o",  # ο
    "ρ": "p",  # ρ
    "τ": "t",  # τ
    "χ": "x",  # χ
    # uppercase
    "Α": "a",  # Α
    "Β": "b",  # Β
    "Ε": "e",  # Ε
    "Η": "h",  # Η
    "Ι": "i",  # Ι
    "Κ": "k",  # Κ
    "Μ": "m",  # Μ
    "Ν": "n",  # Ν
    "Ο": "o",  # Ο
    "Ρ": "p",  # Ρ
    "Τ": "t",  # Τ
    "Υ": "y",  # Υ
    "Χ": "x",  # Χ
    "Ζ": "z",  # Ζ
}

# ── A few Latin-block / IPA twins NFKC leaves alone ──────────────────────────
# (Roman numerals ⅰ/ⅼ/ⅹ are intentionally omitted — NFKC already folds them.)
_MISC = {
    "ı": "i",  # ı dotless i
    "ɡ": "g",  # ɡ script g (IPA)
    "ǀ": "l",  # ǀ dental click (resembles l / |)
}

_CONFUSABLE_DICT: dict[str, str] = {**_CYRILLIC, **_GREEK, **_MISC}

# Public translation table — drop-in for the old per-file str.maketrans maps.
CONFUSABLE_MAP = str.maketrans(_CONFUSABLE_DICT)

# The set of codepoints this table knows how to fold — used by callers that want
# to *detect* (not fold) the presence of a confusable, e.g. script-mixing checks.
CONFUSABLE_CODEPOINTS = frozenset(_CONFUSABLE_DICT)


def fold(text: str) -> str:
    """Normalise text for confusable-safe matching.

    NFKC (folds math-alnum / fullwidth) → strip combining marks (Mn) and format
    chars (Cf: zero-width spaces, joiners, RTL marks) → map Cyrillic/Greek
    confusables → lowercase. This is the shared primitive; callers layer their
    own extras (leetspeak, punctuation stripping) on top.
    """
    text = unicodedata.normalize("NFKC", text)
    text = "".join(
        c
        for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) not in ("Mn", "Cf")
    )
    return text.translate(CONFUSABLE_MAP).lower()


def skeleton(label: str) -> str:
    """UTS #39-style skeleton for *brand* comparison: fold, then keep only
    ASCII alphanumerics. Two strings with the same skeleton render alike.

    Used to answer "does this IDN label render like <brand>?" — so hyphens,
    dots, and any residual non-ASCII are dropped. Intended for a single DNS
    label, not a whole hostname.
    """
    return "".join(c for c in fold(label) if c.isascii() and c.isalnum())


def decode_idna_host(host: str) -> str:
    """Return the Unicode rendering of a possibly-Punycode host.

    Each `xn--` label is punycode-decoded (stdlib codec, no dependency); labels
    that fail to decode are left verbatim. Total and exception-safe — a garbage
    host round-trips unchanged rather than raising.
    """
    out = []
    for label in host.split("."):
        if label.startswith("xn--"):
            try:
                out.append(label[4:].encode("ascii").decode("punycode"))
            except (UnicodeError, ValueError):
                out.append(label)
        else:
            out.append(label)
    return ".".join(out)


def _registrable_label(host: str) -> str:
    """The label most users read as 'the brand' — the second-from-last label
    (bankofamerica in bankofamerica.com, in login.bankofamerica.com). A bare
    single-label host returns itself. Good enough for skeleton comparison; it is
    not a Public Suffix List lookup (two-level TLDs are handled by matching the
    brand's own registrable label the same way)."""
    labels = [x for x in host.strip(".").split(".") if x]
    if len(labels) >= 2:
        return labels[-2]
    return labels[0] if labels else ""


def homograph_target(host: str, brand_domains: list[str]) -> str | None:
    """If `host` is a Punycode/Unicode homograph impersonation of one of the
    protected `brand_domains`, return the impersonated brand domain; else None.

    The check fires only when the host's registrable label contains a non-ASCII
    (or Punycode-decoded non-ASCII) character AND its skeleton equals a protected
    brand's registrable-label skeleton AND the host is not itself a legitimate
    subdomain/exact-match of that brand. Pure-ASCII typosquats are deliberately
    left to the lexical risk scorer — this function only speaks homographs, which
    keeps its false-positive rate near zero (a legitimate ASCII host never has a
    non-ASCII skeleton to collide with a brand).
    """
    host = host.strip().lower().rstrip(".")
    if not host:
        return None
    decoded = decode_idna_host(host)
    label = _registrable_label(decoded)
    # Only homographs: the label must actually contain a non-ASCII confusable.
    if label.isascii():
        return None
    host_skel = skeleton(label)
    if not host_skel:
        return None
    for brand in brand_domains:
        brand = brand.strip().lower().rstrip(".")
        if not brand:
            continue
        # Legitimate: exact brand or a subdomain of it (compared on the ASCII
        # host, so a genuine bankofamerica.com is never flagged).
        if host == brand or host.endswith("." + brand):
            return None
        if skeleton(_registrable_label(brand)) == host_skel:
            return brand
    return None
