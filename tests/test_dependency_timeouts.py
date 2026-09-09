"""A slow dependency must not hang the request path.

Two failures with the same shape. Redis clients were built as
`from_url(url, decode_responses=True)` — redis-py applies no socket timeout by
default, and no call site wrapped its awaits in wait_for, so a Redis that
accepted the connection and then stalled never returned. The `except Exception`
fallbacks to local RAM buckets could not help: a hang is not an exception.

And verify_token, though async, called PyJWKClient.get_signing_key_from_jwt
directly — synchronous urllib on the event loop, with PyJWT's 30-second default
timeout. A stalled identity provider therefore froze every in-flight request in
the process, not just the one presenting a JWT.
"""

import asyncio
import time

import pytest

from core import redis_client


class _FakeRedisModule:
    """Captures the kwargs a caller passes to from_url."""

    def __init__(self):
        self.kwargs = None

    def from_url(self, url, **kwargs):
        self.kwargs = kwargs
        return object()


def test_timeouts_are_applied_by_default():
    fake = _FakeRedisModule()
    redis_client.connect(fake, "redis://localhost:6379/0")

    assert fake.kwargs["socket_timeout"] == redis_client.DEFAULT_SOCKET_TIMEOUT_S
    assert (
        fake.kwargs["socket_connect_timeout"]
        == redis_client.DEFAULT_CONNECT_TIMEOUT_S
    )
    assert fake.kwargs["decode_responses"] is True


def test_config_overrides_the_defaults():
    fake = _FakeRedisModule()
    config = {"caching": {"redis_socket_timeout": 0.5, "redis_connect_timeout": 0.25}}
    redis_client.connect(fake, "redis://localhost:6379/0", config)

    assert fake.kwargs["socket_timeout"] == 0.5
    assert fake.kwargs["socket_connect_timeout"] == 0.25


def test_a_zero_timeout_is_refused_rather_than_honoured():
    """Zero means "wait forever" to redis-py — the behaviour being removed."""
    fake = _FakeRedisModule()
    redis_client.connect(fake, "redis://x", {"caching": {"redis_socket_timeout": 0}})
    assert fake.kwargs["socket_timeout"] == redis_client.DEFAULT_SOCKET_TIMEOUT_S

    redis_client.connect(fake, "redis://x", {"caching": {"redis_socket_timeout": -1}})
    assert fake.kwargs["socket_timeout"] == redis_client.DEFAULT_SOCKET_TIMEOUT_S

    redis_client.connect(fake, "redis://x", {"caching": {"redis_socket_timeout": "no"}})
    assert fake.kwargs["socket_timeout"] == redis_client.DEFAULT_SOCKET_TIMEOUT_S


def test_env_var_tunes_it_where_no_config_is_in_reach(monkeypatch):
    monkeypatch.setenv("LLM_PROXY_REDIS_TIMEOUT", "0.75")
    fake = _FakeRedisModule()
    redis_client.connect(fake, "redis://x")
    assert fake.kwargs["socket_timeout"] == 0.75
    assert fake.kwargs["socket_connect_timeout"] == 0.75


def test_every_redis_construction_site_goes_through_the_helper():
    """The three call sites, checked as source rather than one by one.

    Each used to call from_url directly. If a fourth appears, or one of these
    reverts, this fails — which is the only way a timeout stays applied.
    """
    import pathlib

    offenders = []
    for path in ("core/rate_limiter.py", "core/circuit_breaker.py", "proxy/rotator.py"):
        src = pathlib.Path(path).read_text()
        for lineno, line in enumerate(src.splitlines(), 1):
            if ".from_url(" in line and "redis_client" not in line:
                offenders.append(f"{path}:{lineno}: {line.strip()}")

    assert not offenders, (
        "Redis clients built without the timeout helper:\n  " + "\n  ".join(offenders)
    )


def test_the_rate_limiter_passes_its_config_through(monkeypatch):
    """End to end for the site on the hottest path."""
    import core.rate_limiter as rl

    captured = {}

    class _Mod:
        def from_url(self, url, **kwargs):
            captured.update(kwargs)
            return object()

    monkeypatch.setattr(rl, "redis", _Mod())
    rl.RateLimiter(
        redis_url="redis://localhost:6379/0",
        config={"caching": {"redis_socket_timeout": 1.5}},
    )
    assert captured.get("socket_timeout") == 1.5


@pytest.mark.asyncio
async def test_jwks_fetch_does_not_block_the_event_loop():
    """The loop must keep turning while a stalled provider is being waited on.

    A tick counter runs concurrently with verify_token. If the JWKS fetch were
    still called directly, the loop would be blocked for its whole duration and
    the counter would not advance.
    """
    from core.identity import IdentityManager, OIDCProvider

    manager = IdentityManager({"identity": {"enabled": True}})

    class _StallingJWKSClient:
        def get_signing_key_from_jwt(self, token):
            time.sleep(0.30)  # a provider that accepts and then stalls
            raise RuntimeError("provider stalled")

    provider = OIDCProvider(
        name="stub",
        issuer="https://issuer.example",
        client_id="cid",
        jwks_uri="https://issuer.example/jwks",
        audience="cid",
    )
    manager.enabled = True
    manager.providers = {"stub": provider}
    manager._get_jwks_client = lambda _p: _StallingJWKSClient()

    ticks = 0

    async def _tick():
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.01)

    import jwt as _jwt

    token = _jwt.encode(
        {"iss": "https://issuer.example", "sub": "u"}, "secret", algorithm="HS256"
    )

    ticker = asyncio.create_task(_tick())
    try:
        with pytest.raises(Exception):
            await manager.verify_token(token)
    finally:
        ticker.cancel()

    assert ticks > 5, (
        f"the event loop advanced only {ticks} times during a 300 ms JWKS fetch — "
        "it is still being called on the loop"
    )


def test_the_jwks_client_is_built_with_a_bounded_timeout():
    """PyJWT's default is 30 s; on a request path that is not a bound."""
    from core.identity import JWKS_FETCH_TIMEOUT_S

    assert 0 < JWKS_FETCH_TIMEOUT_S <= 10
