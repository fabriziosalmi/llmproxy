# Performance

This page reports what the benchmark suite in `tests/test_benchmarks.py` measures, and what it does not.

## Scope of the figures

- Every figure is the mean reported by `pytest-benchmark` for one test, named in the table.
- They were measured in one run on one developer laptop (Apple M4, Python 3.14, 2026-10-10). Treat them as orders of magnitude and re-run on your own hardware.
- Each benchmark times one function, or a few, called directly in the test process. None of them sends a request through the proxy.
- The suite contains no end-to-end measurement of the latency the proxy adds to a request. The module's docstring names a target of under 5 ms P99 per request; no benchmark measures a percentile or a whole request, so the suite does not check that target.

`pytest-benchmark` is listed in `requirements-dev.txt`.

## Regex threat score and firewall scan

Reproduce: `pytest tests/test_benchmarks.py::TestSecurityOverheadBenchmarks --benchmark-only -v`

These tests call `SecurityShield._calculate_threat_score` (30 regular expressions) and `ByteLevelFirewallMiddleware._scan_payload` on a prompt held in memory.

| Test | What is timed | Mean |
|------|---------------|-----:|
| `test_threat_score_clean_short` | Threat score, a 6-word benign prompt | ~9 µs |
| `test_threat_score_attack` | Threat score, a one-sentence attack prompt in Italian | ~14 µs |
| `test_threat_score_clean_long` | Threat score, a 1000-word benign prompt | ~850 µs |
| `test_full_deterministic_decision_clean` | Firewall scan plus threat score, a one-sentence benign prompt | ~31 µs |
| `test_full_deterministic_decision_attack` | Firewall scan plus threat score, a one-sentence attack prompt in Italian | ~33 µs |

The threat score runs every pattern over the prompt; it does not stop at the first match. It scans a normalised copy of the text and, when that copy differs from the lower-cased original, the original as well. For plain ASCII text the two are identical and the patterns run once.

## Semantic scan

Reproduce: `pytest tests/test_benchmarks.py::TestSemanticAnalyzerBenchmarks --benchmark-only -v`

The shield also runs the semantic scan (`core.semantic_analyzer.semantic_scan`) on every prompt that the regex score has not already blocked, unless `semantic_analysis.enabled` is false in the shield's configuration (it is on by default). It runs in a worker thread with a 5-second timeout. Its cost is therefore part of an ordinary request and is not included in the table above.

| Test | What is timed | Mean |
|------|---------------|-----:|
| `test_scan_clean_short` | Semantic scan, a 5-word prompt | ~9 µs |
| `test_scan_clean_medium` | Semantic scan, a medium prompt | ~32 µs |
| `test_scan_clean_long` | Semantic scan, a 1000-word prompt | ~700 µs |

The AI escalation runs only when the combined score falls in the range the shield escalates. It is not benchmarked.

## Routing: choosing an endpoint

Reproduce: `pytest tests/test_benchmarks.py::TestRoutingBenchmarks --benchmark-only -v`

`select_endpoint` (Ring 3) runs for every proxied request. The store serves it a snapshot of the verified endpoint pool. Every endpoint write through the store invalidates the snapshot, and it expires after 5 seconds.

The benchmark calls `select_endpoint` against a local SQLite file, with an in-process circuit manager and no Redis. "Cached" reuses the snapshot. "Cold" invalidates it before each call, so the pool is read from the database.

| Endpoints in pool | Cached | Cold |
|------------------:|-------:|-----:|
| 1  | ~31 µs | ~119 µs |
| 10 | ~35 µs | ~154 µs |
| 50 | ~49 µs | ~289 µs |

The benchmark involves no network: the database is a local file and the circuit manager is in the same process. A deployment on PostgreSQL or with a Redis circuit manager adds round trips that are not measured here.

## Other benchmarks in the file

| Class | What it times |
|-------|---------------|
| `TestRateLimiterBenchmarks` | The in-process `TokenBucket` of `core/rate_limiter.py`. Not the Redis path |
| `TestCacheKeyBenchmarks` | Cache key computation |
| `TestFirewallBenchmarks` | Firewall normalisation and scan |
| `TestPricingBenchmarks` | Price lookup and cost calculation |
| `TestTrigramBenchmarks` | Trigram extraction and Jaccard similarity |
| `TestSerializationBenchmarks` | JSON serialisation of a response and parsing of a request body |
| `TestAuditChainBenchmarks` | A SHA-256 over a sample string, computed in the test. It does not call the store's audit code |
| `TestRoutingFanoutBenchmarks` | Circuit-breaker filtering over 20 and 50 endpoints, with a fake circuit manager that sleeps 0.2 ms per call |

Run all of them with `make bench`, or `pytest tests/test_benchmarks.py --benchmark-only -v`.

## What the figures do not include

A request through the proxy also pays for authentication, JSON parsing, the plugin rings (PII masking, budget check, cache lookup and the rest), the audit write and the network. None of these is covered by a benchmark in this file.
