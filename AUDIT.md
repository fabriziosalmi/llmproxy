# AUDIT -- Improvement Tracker

**v1 Baseline**: 81/100 (Brutal Audit, Gemini Pro)
**v2 Score**: 91/100 (after P0+P1+P2)
**Target**: 95+/100

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

- [x] Add `mypy` step to `.github/workflows/ci.yml` (non-blocking, 48 errors tracked — tighten incrementally)
- [ ] Fix all mypy errors (48 remaining, mostly implicit Optional)
- [ ] Add `py.typed` marker file for type-checking consumers

### CI/CD Pipeline [4/5]

- [ ] Add k6 load test script (`tests/load/`) with baseline latency + throughput assertions
- [ ] Add k6 step to GitHub Actions CI (run against in-process test server)
- [x] Add `pip-audit` to CI for Python dependency vulnerability scanning

### State Management [4/5]

- [x] Abstract state backend behind a `StateBackend` protocol (SQLite implements it, Redis can later)
- [x] Add write coalescing for budget updates -- batch fire-and-forget writes into periodic 1s flushes (done in P0)
- [ ] Add WAL checkpoint configuration for SQLite under concurrent write load

### Dependency Management [4/5]

- [x] Pin all dependency versions in requirements.txt (core deps pinned to exact versions)
- [ ] Audit and remove unused optional dependencies
- [x] Add `pip-audit` output to CI artifacts

---

## P2: Scale Ceiling (3/5 -> 4/5, higher effort)

### Latency Overhead [3/5]

- [ ] Profile the full 5-ring pipeline under load with `py-spy` -- identify the actual P99 bottleneck before optimizing
- [x] Add connection pooling configuration for upstream aiohttp sessions (TCPConnector: 100 total, 20/host, DNS cache 5min)
- [ ] Evaluate PyO3 Rust extension for the ASGI firewall byte scanner (only if profiling proves it is the bottleneck)

### Database Bottlenecks [3/5]

- [ ] Benchmark audit log write throughput under concurrent load -- quantify the actual SQLite ceiling
- [x] Add SQLite WAL mode, synchronous=NORMAL, busy_timeout=5000ms pragmas for concurrent write performance
- [ ] Document the scaling path: when and how to migrate from SQLite to Redis/Postgres

### Routing Intelligence [3/5]

- [x] Cache EMA scores per-endpoint in a dict -- already implemented in `_endpoint_stats` module-level dict (neural_router.py)
- [x] Pre-compute routing decisions for static model aliases -- already O(1) dict lookup (model_resolver.py)

### Observability [3/5]

- [x] Add SSE connection limit and stale consumer eviction (20 max per stream, disconnect detection, keep-alive)
- [x] Add per-ring latency histograms to Prometheus -- already exists (`RING_LATENCY` in core/metrics.py:60-65)
- [x] Add health check for SSE stream endpoints (disconnect detection via `request.is_disconnected()`)

---

## P3: Advanced (v2 audit PHASE 3 + v1 carryover)

### Quick Wins (can do now)

- [ ] Add Dependabot config for automated dependency updates (`.github/dependabot.yml`)
- [ ] Fix remaining 48 mypy errors (mostly `implicit Optional` -> `X | None` syntax)
- [ ] Add `py.typed` marker file
- [ ] Audit and remove unused optional dependencies from requirements.txt

### Observability & Tracing

- [ ] Add W3C Trace Context (`traceparent`) header propagation to SOC UI requests for full-stack OpenTelemetry visibility
- [ ] Add distributed trace correlation IDs in SSE log stream entries

### Chaos Engineering & Testing

- [ ] Add Toxiproxy chaos tests in CI proving circuit breakers and fallback chains work under network partitions
- [ ] Add integration test: SIGTERM during in-flight streaming request (verify graceful shutdown)
- [ ] Add property-based tests (Hypothesis) for PII detection edge cases
- [ ] Add fuzz testing for the ASGI firewall byte scanner
- [ ] Add k6 load test script with baseline latency + throughput assertions

### Scale (when load demands it)

- [ ] Migrate state from SQLite to PostgreSQL/Redis for horizontal scalability
- [ ] Implement distributed Token Bucket rate limiter via Redis (multi-node deployments)
- [ ] Configure Litestream/LiteFS for SQLite WAL replication to S3 (if staying with SQLite)
- [ ] Add Dead-Letter Queue for telemetry/audit export queues (prevent data loss during storage outages)

### Security (research)

- [ ] Evaluate lightweight ONNX model for semantic injection detection (supplement regex firewall)
- [ ] Profile ASGI firewall under load, evaluate PyO3 Rust extension if it is the bottleneck

### Performance

- [ ] Profile full 5-ring pipeline under load with `py-spy` -- identify actual P99 bottleneck
- [ ] Benchmark audit log write throughput under concurrent load -- quantify SQLite ceiling
- [ ] Evaluate aiohttp -> httpx migration for HTTP/2 multiplexing
- [ ] Add Docker image size tracking in CI

---

## Conscious Trade-offs (not doing now)

| Item | Reason |
|------|--------|
| Rewrite core in Rust (PyO3) | Only justified at >10K req/s sustained. Profile first. |
| Redis migration now | SQLite + WAL handles current scale. `StateBackend` protocol ready for swap. |
| aiohttp -> httpx now | Benchmark HTTP/2 benefit first. Don't migrate for theoretical gains. |
| ONNX injection model now | Requires training data, latency budget analysis, and model validation. Research phase. |
| DLQ for telemetry | Bounded queues with FIFO eviction handle current scale. DLQ adds complexity without current need. |

---

## Scorecard

| Category | v1 Audit | v2 Audit | Target | Status |
|----------|----------|----------|--------|--------|
| Concurrency & Event Loop | 3/5 | 5/5 | 5/5 | Done (P0) |
| SOC UI Hardening | --/5 | 5/5 | 5/5 | Done (P0) |
| Type Safety | 4/5 | 4/5 | 5/5 | mypy in CI, 48 errors remaining |
| CI/CD Pipeline | 4/5 | 5/5 | 5/5 | Done (P1) |
| State Management | 4/5 | 4/5 | 5/5 | Protocol ready, WAL tuned |
| Dependency Management | 4/5 | 4/5 | 5/5 | Pinned, pip-audit in CI |
| Latency Overhead | 3/5 | 4/5 | 5/5 | Pool tuned, profile pending |
| Database Bottlenecks | 3/5 | 4/5 | 5/5 | WAL tuned, benchmark pending |
| Routing Intelligence | 3/5 | 5/5 | 5/5 | Done (already optimal) |
| Observability | 3/5 | 4/5 | 5/5 | SSE limits done, trace IDs pending |
| **Score** | **81** | **91** | **95+** | **20 P3 items remaining** |
