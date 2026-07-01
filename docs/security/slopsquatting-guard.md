# AI Dependency Guard — slopsquatting defense

**Plugin:** `installed.ai_dependency_guard:AiDependencyGuard` · **Hook:** `post_flight` · **Default:** disabled (opt-in)

A port of [ai-dependency-guard](https://github.com/fabriziosalmi/ai-dependency-guard),
moved from CI manifest scanning to **live inspection of the model's own output**.

## The attack: slopsquatting

LLMs hallucinate package names. When a model confidently answers *"just run
`pip install <x>`"* for a package that doesn't exist, an attacker can register
that exact name on PyPI/npm and ship malware — the user who copy-pastes the
suggestion installs it. The hallucinated name is **not random**: the same fake
recurs across generations of the same prompt, which is what makes
pre-registration profitable. Recent research measured ~20% of LLM-suggested
packages as non-existent across popular models.

`ai_dependency_guard` inspects each response, extracts the packages the model
told the user to install, and flags the ones that don't exist (or are
denylisted) **before the user acts on them**.

## Two-tier detection (faithful to upstream)

| Tier | Check | Cost | Catches |
|------|-------|------|---------|
| **1** | Configured **blocklist** (offline) | instant | squatted/hijacked names that *do* resolve to HTTP 200 |
| **2** | **Registry existence** — PyPI JSON API / npm registry | one HTTP HEAD-ish GET/pkg, cached | hallucinated names (strict HTTP **404**) |

**Fail-open, always.** A registry `200`, any `5xx`, a timeout, or any transport
error is treated as *"the package exists"* — availability over strict validation,
exactly as upstream. Only a strict `404` flags a package. Tier-2 is bounded by a
total time budget (`registry_timeout_ms`, default 1500 ms); on overrun the sweep
is abandoned and nothing extra is flagged.

## What counts as a "package reference"

An LLM response is prose, not a manifest, so extraction is deliberately
high-precision — only these forms are read:

- **install commands:** `pip install X`, `pip3 install X`, `python -m pip install X`, `npm install/i/add X`, `yarn add X`, `pnpm add X`
- **pinned requirement lines:** `name==1.2.3`, `name>=1.0` (a version specifier is *required*, so ordinary prose never matches)

Flags and their path arguments are dropped (`-r requirements.txt`, `-e ./local`,
`--index-url …`), as are URLs, `git+…` refs, local paths, and `.whl`/`.tgz`
tarballs. Names are normalized (lowercase, extras/specifiers stripped; PyPI names
[PEP 503](https://peps.python.org/pep-0503/)-canonicalized for the lookup). npm
scoped packages (`@scope/name`) are preserved and URL-encoded for the registry.

## Action

By default the plugin is **non-blocking**: it enriches response metadata and logs
a warning, leaving the response untouched so a downstream plugin or the SOC can
decide. Set `block_on_hallucination: true` to reject the response with `403`.

```
ctx.metadata["_depguard_flagged"]  = True
ctx.metadata["_depguard_packages"] = [
    {"name": "totally-not-real-pkg", "ecosystem": "pypi", "reason": "not_found"},
    {"name": "evil-pkg",             "ecosystem": "pypi", "reason": "blocklist"},
]
```

## Configuration

```yaml
- name: "AI Dependency Guard"
  enabled: true                      # opt-in
  config:
    registry_check: true             # Tier 2 on/off
    block_on_hallucination: false    # true → 403 the response
    max_packages_per_response: 20    # cap the network fan-out
    registry_timeout_ms: 1500        # total Tier-2 time budget (fail-open on overrun)
    cache_ttl_s: 3600                # existence results are cached process-wide
    ecosystems: ["pypi", "npm"]
    blocklist: []                    # your known-bad names (checked offline, Tier 1)
```

The blocklist ships **empty** on purpose — a bundled malware denylist goes stale
and risks false accusations, so it's yours to populate per deployment (mirroring
upstream's `--blocklist`). Tier 2 is the always-on engine; Tier 1 is for names
that resolve to `200` but you know are bad.

## Tests

`tests/test_ai_dependency_guard.py` — extraction precision (prose, flags, URLs,
scoped npm, pinned requirements), Tier-1 blocklist, Tier-2 404 detection
(cache-seeded, offline), block-on-hallucination, and the fail-open / cheap-exit
guarantees.
