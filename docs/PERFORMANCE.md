# Performance — the cost of security

LLMProxy adds a defense-in-depth pipeline to every request. This page quantifies
what that costs, because "is it secure?" and "is it fast enough?" are both buying
questions. Numbers below are **reproducible** — they come from a committed
benchmark suite, not a slide.

Reproduce: `pytest tests/test_benchmarks.py::TestSecurityOverheadBenchmarks --benchmark-only -v`

> Absolute figures are machine-dependent (measured on a developer laptop); treat
> them as orders of magnitude and re-run on your target hardware. What matters is
> the *shape*: the per-request security cost is tens of microseconds, far below
> the network/inference latency that dominates an LLM call.

## Deterministic security overhead per request

The hot path every legitimate request pays is the **ASGI byte-firewall scan** plus
the **regex threat-score**. (The optional async semantic scan and AI gray-zone
escalation run only for borderline inputs and are excluded here.)

| Path | Mean | Notes |
|------|-----:|-------|
| Regex threat-score — short benign prompt | **~8 µs** | 30+ patterns over raw+normalized text |
| Regex threat-score — attack prompt | ~13 µs | short-circuits on first strong match |
| Regex threat-score — 1200-word prompt | ~760 µs | scales with length; single-pass for ASCII (see note) |
| **Firewall + threat-score — clean request** | **~27 µs** | the real per-request cost |
| Firewall + threat-score — multilingual attack | ~29 µs | resolves to a block |

**Takeaway:** ~27 µs of deterministic security work on a typical request, and
sub-millisecond even on a very long prompt — comfortably under the suite's
< 5 ms P99 target, and negligible next to the 100s of ms of upstream inference.

## A note on the raw+normalized dual scan

Threat-score scanning runs over both the raw text and a homoglyph-normalized copy
so that Latin-obfuscated *and* native non-Latin (CJK/Cyrillic) injections are both
caught. For pure-ASCII English — the common case — the two forms are identical, so
the scanner detects that and makes a single pass, cutting the long-prompt cost by
~35 % (1.17 ms → 0.76 ms) with no loss of detection.

## Routing: choosing an endpoint

Ring 3 (`select_endpoint`) runs for every proxied request. It used to read the
whole verified endpoint pool from the database each time (a `SELECT`, a
`json.loads` and a pydantic model per row), so its cost grew with the number of
registered endpoints. The store now serves a snapshot of the pool that every
endpoint write (add, remove, status, metrics) invalidates, with a 5-second TTL
as a backstop for a writer the store cannot see.

Reproduce: `pytest tests/test_benchmarks.py::TestRoutingBenchmarks --benchmark-only -v`

| Endpoints in pool | Cached (steady state) | Cold (what every request used to pay) |
|------------------:|----------------------:|--------------------------------------:|
| 1  | ~49 µs  | ~138 µs |
| 10 | ~51 µs  | ~158 µs |
| 50 | ~58 µs  | ~300 µs |

Measured on a developer laptop against a local SQLite file; with the Postgres
store the cold path additionally pays a network round trip, which is the part
the cache removes. Treat the figures as orders of magnitude and re-run on your
hardware.

## What is not on the hot path

- **Semantic scan** (trigram Jaccard) and **AI escalation** run only for gray-zone
  scores, on a thread pool / with a timeout — they never block the fast path.
- **Rate limiting / circuit breaking** are Redis Lua ops at the ASGI edge (see the
  buckets benchmarks in the same suite).
- **Audit hash-chain** appends are O(1) SHA-256 (see the audit benchmarks).
