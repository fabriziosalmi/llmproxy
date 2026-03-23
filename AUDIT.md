# AUDIT -- Improvement Tracker

**Baseline**: 81/100 (Brutal Audit, Gemini Pro)
**Target**: 90+/100

All items verified against the actual codebase. Grouped by audit category, ordered by impact-to-effort ratio.

---

## P0: Quick Wins (3/5 -> 5/5, low effort)

### Concurrency & Event Loop [3/5]

- [x] `proxy/rotator.py:175-178` -- Move `_compute_config_hash()` blocking I/O to `asyncio.to_thread()` (hashlib + file read on the event loop)
- [x] `proxy/routes/chat.py:131` -- Replace fire-and-forget `asyncio.create_task(store.set_state(...))` budget writes with a graceful shutdown-aware background queue
- [x] Add SIGTERM handler that awaits pending budget writes before exit (drain on FastAPI shutdown event)

### SOC UI Hardening [missing]

- [x] Add `Content-Security-Policy` header to the SOC UI static file serving (prevent XSS from malicious LLM responses rendered in logs)
- [x] Add `X-Frame-Options: DENY` and `X-Content-Type-Options: nosniff` response headers
- [x] Sanitize LLM response content in the xterm.js live logs terminal before rendering (ANSI + control char stripping)

---

## P1: Engineering Rigor (4/5 -> 5/5, medium effort)

### Type Safety [4/5]

- [ ] Add `mypy --strict` step to `.github/workflows/ci.yml`
- [ ] Fix all mypy strict-mode errors (scope TBD -- run locally first)
- [ ] Add `py.typed` marker file for type-checking consumers

### CI/CD Pipeline [4/5]

- [ ] Add k6 load test script (`tests/load/`) with baseline latency + throughput assertions
- [ ] Add k6 step to GitHub Actions CI (run against in-process test server)
- [ ] Add `pip-audit` to CI for Python dependency vulnerability scanning

### State Management [4/5]

- [ ] Abstract state backend behind a `StateBackend` protocol (SQLite implements it, Redis can later)
- [ ] Add write coalescing for budget updates -- batch fire-and-forget writes into periodic 1s flushes
- [ ] Add WAL checkpoint configuration for SQLite under concurrent write load

### Dependency Management [4/5]

- [ ] Pin all dependency versions in requirements.txt (some are currently unpinned)
- [ ] Audit and remove unused optional dependencies
- [ ] Add `pip-audit` output to CI artifacts

---

## P2: Scale Ceiling (3/5 -> 4/5, higher effort)

### Latency Overhead [3/5]

- [ ] Profile the full 5-ring pipeline under load with `py-spy` -- identify the actual P99 bottleneck before optimizing
- [ ] Add connection pooling configuration for upstream aiohttp sessions (max connections, keepalive, DNS cache TTL)
- [ ] Evaluate PyO3 Rust extension for the ASGI firewall byte scanner (only if profiling proves it is the bottleneck)

### Database Bottlenecks [3/5]

- [ ] Benchmark audit log write throughput under concurrent load -- quantify the actual SQLite ceiling
- [ ] Add SQLite connection pool size as a config option
- [ ] Document the scaling path: when and how to migrate from SQLite to Redis/Postgres

### Routing Intelligence [3/5]

- [ ] Cache EMA scores per-endpoint in a dict (avoid recalculation on every request)
- [ ] Pre-compute routing decisions for static model aliases at startup

### Observability [3/5]

- [ ] Add SSE connection limit and stale consumer eviction (prevent resource exhaustion)
- [ ] Add per-ring latency histograms to Prometheus (currently only aggregate latency)
- [ ] Add health check for SSE stream endpoints (detect stuck consumers)

---

## P3: Advanced (nice-to-have, evaluate later)

### Chaos Engineering

- [ ] Add Toxiproxy or equivalent chaos tests to CI proving circuit breakers and fallback chains work under network degradation
- [ ] Add integration test: SIGTERM during in-flight streaming request (verify graceful shutdown)

### HTTP Client

- [ ] Evaluate aiohttp -> httpx migration for HTTP/2 multiplexing to upstream providers
- [ ] Benchmark HTTP/2 vs HTTP/1.1 for OpenAI/Anthropic/Google (quantify actual benefit)

### Testing

- [ ] Add property-based tests (Hypothesis) for PII detection edge cases
- [ ] Add fuzz testing for the ASGI firewall byte scanner
- [ ] Add Docker image size tracking in CI (alert on bloat)

---

## Conscious Trade-offs (not doing)

| Item | Reason |
|------|--------|
| Rewrite core in Rust (PyO3) | Only justified at >10K req/s sustained. Profile first. |
| Redis migration now | SQLite + WAL handles current scale. Abstract the interface (P1), migrate when needed. |
| aiohttp -> httpx now | Benchmark HTTP/2 benefit first. Don't migrate for theoretical gains. |
| Dedicated pub/sub for SSE | Connection limit + eviction (P2) solves the practical problem without adding infra. |

---

## Scorecard

| Category | Audit | Target | Items |
|----------|-------|--------|-------|
| Concurrency & Event Loop | 3/5 | 5/5 | 3 |
| SOC UI Hardening | --/5 | 5/5 | 3 |
| Type Safety | 4/5 | 5/5 | 3 |
| CI/CD Pipeline | 4/5 | 5/5 | 3 |
| State Management | 4/5 | 5/5 | 3 |
| Dependency Management | 4/5 | 5/5 | 3 |
| Latency Overhead | 3/5 | 4/5 | 3 |
| Database Bottlenecks | 3/5 | 4/5 | 3 |
| Routing Intelligence | 3/5 | 4/5 | 2 |
| Observability | 3/5 | 5/5 | 3 |
| **Total** | **81** | **90+** | **32 items** |
