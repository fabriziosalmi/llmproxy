"""The JWKS fetch was moved off the event loop and onto everyone else's threads.

1.34.0 fixed a real hang: PyJWKClient.get_signing_key_from_jwt fetches over
synchronous urllib, and calling it directly from verify_token blocked the
single event loop for the whole fetch — one slow identity provider stalled
every in-flight request, not just the one authenticating.

The fix was asyncio.to_thread, which submits to the loop's DEFAULT executor.
That pool is shared with the event-log DLQ writes, the config-file hashing in
the watcher, the semantic-cache lookups and the shield's regex scans. So the
blocking moved rather than went away: a stalled provider now occupied threads
that unrelated blocking work needed, and with nothing coalescing concurrent
cache misses, N simultaneous logins after a restart or a key rotation fired N
identical fetches, each holding one of those threads for up to the fetch
timeout.

core/wasm_runner.py already made the same call for the same reason — its
comment says so explicitly — so the codebase had the precedent and this path
missed it.
"""

import asyncio
import threading
import time

import pytest

import core.identity as identity
from core.identity import (
    JWKS_FETCH_TIMEOUT_S,
    JWKS_TOTAL_BUDGET_S,
    IdentityManager,
    OIDCProvider,
)


def _manager():
    mgr = IdentityManager({"identity": {"enabled": True}})
    mgr.enabled = True
    provider = OIDCProvider(
        name="acme",
        issuer="https://acme.example",
        jwks_uri="https://acme.example/jwks",
        client_id="cid",
        audience="cid",
    )
    mgr.providers["acme"] = provider
    return mgr, provider


class _SlowClient:
    """Stands in for PyJWKClient: records concurrency, sleeps like a fetch."""

    def __init__(self, delay=0.2):
        self.delay = delay
        self.calls = 0
        self.concurrent = 0
        self.max_concurrent = 0
        self.threads = set()
        self._guard = threading.Lock()

    def get_signing_key_from_jwt(self, token):
        with self._guard:
            self.calls += 1
            self.concurrent += 1
            self.max_concurrent = max(self.max_concurrent, self.concurrent)
            self.threads.add(threading.current_thread().name)
        try:
            time.sleep(self.delay)
            return f"key-for-{token}"
        finally:
            with self._guard:
                self.concurrent -= 1


# ── the fetch does not run on the pool everything else shares ───────────────


@pytest.mark.asyncio
async def test_the_fetch_runs_on_a_dedicated_pool(monkeypatch):
    """The default executor serves the DLQ writer, the config watcher and the
    cache. A third-party HTTP fetch does not belong in it."""
    mgr, provider = _manager()
    client = _SlowClient(delay=0.05)
    monkeypatch.setattr(mgr, "_get_jwks_client", lambda p: client)

    await mgr._signing_key(provider, "tok")

    assert client.threads, "the fetch never ran"
    assert all(
        name.startswith("jwks") for name in client.threads
    ), f"ran on a shared pool thread: {client.threads}"


@pytest.mark.asyncio
async def test_the_default_executor_is_left_alone(monkeypatch):
    """Directly: whatever thread the fetch uses, it must not be one that
    asyncio.to_thread would hand to unrelated blocking work."""
    mgr, provider = _manager()
    client = _SlowClient(delay=0.05)
    monkeypatch.setattr(mgr, "_get_jwks_client", lambda p: client)

    default_pool_threads = set()

    def _sample():
        default_pool_threads.add(threading.current_thread().name)

    await asyncio.to_thread(_sample)
    await mgr._signing_key(provider, "tok")

    assert not (client.threads & default_pool_threads)


def test_the_pool_is_released_on_shutdown():
    import inspect

    import proxy.app_factory as app_factory

    assert callable(identity.shutdown_jwks_executor)
    assert "shutdown_jwks_executor" in inspect.getsource(app_factory)


# ── concurrent misses coalesce into one fetch ───────────────────────────────


@pytest.mark.asyncio
async def test_concurrent_misses_produce_one_fetch(monkeypatch):
    """A restart or a key rotation puts every in-flight login on a cache miss
    at the same instant. Twenty logins must not become twenty fetches of the
    same bytes, each holding a thread."""
    mgr, provider = _manager()
    client = _SlowClient(delay=0.1)
    monkeypatch.setattr(mgr, "_get_jwks_client", lambda p: client)

    results = await asyncio.gather(
        *[mgr._signing_key(provider, "tok") for _ in range(20)]
    )

    assert client.max_concurrent == 1, (
        f"{client.max_concurrent} fetches ran at once — nothing coalesced them"
    )
    assert all(r == "key-for-tok" for r in results), "a caller got the wrong key"


@pytest.mark.asyncio
async def test_every_caller_still_gets_a_key(monkeypatch):
    """Coalescing must not starve the callers that waited."""
    mgr, provider = _manager()
    client = _SlowClient(delay=0.02)
    monkeypatch.setattr(mgr, "_get_jwks_client", lambda p: client)

    results = await asyncio.gather(
        *[mgr._signing_key(provider, f"tok-{i}") for i in range(5)]
    )

    assert sorted(results) == sorted(f"key-for-tok-{i}" for i in range(5))


@pytest.mark.asyncio
async def test_different_providers_do_not_block_each_other(monkeypatch):
    """The lock is per provider: a stalled IdP must not gate an unrelated one."""
    mgr, first = _manager()
    second = OIDCProvider(
        name="other",
        issuer="https://other.example",
        jwks_uri="https://other.example/jwks",
        client_id="cid2",
        audience="cid2",
    )
    mgr.providers["other"] = second

    clients = {"acme": _SlowClient(delay=0.15), "other": _SlowClient(delay=0.15)}
    monkeypatch.setattr(mgr, "_get_jwks_client", lambda p: clients[p.name])

    started = time.monotonic()
    await asyncio.gather(
        mgr._signing_key(first, "a"), mgr._signing_key(second, "b")
    )
    elapsed = time.monotonic() - started

    assert elapsed < 0.28, f"the two providers serialised ({elapsed:.2f}s)"


# ── the queue in front of a stalled provider has a ceiling ──────────────────


@pytest.mark.asyncio
async def test_a_stalled_provider_does_not_hold_a_request_forever(monkeypatch):
    """Coalescing turns concurrent misses into a queue, and a queue with no
    ceiling in front of a hung provider is the original hang one level out."""
    mgr, provider = _manager()

    class _Hung:
        def get_signing_key_from_jwt(self, token):
            # Far past the budget, but short enough that the abandoned thread
            # does not stall the suite's teardown when the pool is joined.
            time.sleep(1.0)

    monkeypatch.setattr(mgr, "_get_jwks_client", lambda p: _Hung())
    monkeypatch.setattr(identity, "JWKS_TOTAL_BUDGET_S", 0.15)

    started = time.monotonic()
    with pytest.raises(ValueError, match="unavailable"):
        await mgr._signing_key(provider, "tok")
    elapsed = time.monotonic() - started

    assert elapsed < 1.0, f"waited {elapsed:.2f}s"


@pytest.mark.asyncio
async def test_the_timeout_fails_closed(monkeypatch):
    """A provider that cannot be reached must refuse the token, never admit it."""
    mgr, provider = _manager()

    class _Hung:
        def get_signing_key_from_jwt(self, token):
            # Far past the budget, but short enough that the abandoned thread
            # does not stall the suite's teardown when the pool is joined.
            time.sleep(1.0)

    monkeypatch.setattr(mgr, "_get_jwks_client", lambda p: _Hung())
    monkeypatch.setattr(identity, "JWKS_TOTAL_BUDGET_S", 0.1)

    with pytest.raises(ValueError):
        await mgr._signing_key(provider, "tok")


def test_the_budget_covers_a_queued_fetch_plus_its_own():
    """A caller that arrives just behind another's fetch must be able to wait
    for it AND still get its own attempt, or the ceiling would reject callers
    that were about to succeed."""
    assert JWKS_TOTAL_BUDGET_S >= 2 * JWKS_FETCH_TIMEOUT_S
