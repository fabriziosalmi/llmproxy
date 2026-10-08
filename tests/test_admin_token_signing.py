"""Tokens that open admin-tier routes are not signed with an inference key.

The SSE token (which admits /api/v1/logs, a feed of SECURITY events and user
emails) fell back to ``keys[0]`` of the inference bag when no signing secret was
configured. Anyone holding an inference key, the lowest credential the proxy
issues, could sign a token and read the admin log stream; the verifier also
accepted any expiry, so a forged token could be made to last for decades. The
config confirm token had the same fallback. Both now use a per-process random
secret unless one is configured, and an expiry beyond what the proxy mints is
refused.
"""

import asyncio
import hashlib
import hmac
import time
from types import SimpleNamespace

import httpx
import pytest

from proxy.routes import telemetry
from tests.test_request_path_hangs_and_gaps import (
    ADMIN_KEY,
    INFERENCE_KEY,
    _two_tier_agent,
)


def _token(secret: str, exp: int, nonce: str = "abcdef0123456789") -> str:
    payload = f"{exp}.{nonce}"
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


async def _logs(agent, token: str) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=agent.app), base_url="http://test"
    ) as c:
        return await c.get(f"/api/v1/logs?sse_token={token}")


@pytest.mark.asyncio
async def test_a_token_signed_with_an_inference_key_is_refused():
    agent = _two_tier_agent()
    forged = _token(INFERENCE_KEY, int(time.time()) + 120)

    assert (await _logs(agent, forged)).status_code == 401


@pytest.mark.asyncio
async def test_a_token_signed_with_an_admin_key_is_refused_too():
    """Keys are credentials, not signing material; none of them is the secret."""
    agent = _two_tier_agent()
    forged = _token(ADMIN_KEY, int(time.time()) + 120)

    assert (await _logs(agent, forged)).status_code == 401


@pytest.mark.asyncio
async def test_a_validly_signed_token_with_an_absurd_expiry_is_refused():
    agent = _two_tier_agent()
    long_lived = _token(telemetry._FALLBACK_SSE_SECRET, int(time.time()) + 50 * 365 * 86400)

    assert (await _logs(agent, long_lived)).status_code == 401


@pytest.mark.asyncio
async def test_the_token_the_proxy_mints_is_still_accepted():
    """A refused token answers 401 at once; an accepted one opens the stream,
    which stays open, so "still waiting after a second" is the pass condition."""
    agent = _two_tier_agent()
    agent._event_logger = SimpleNamespace(
        subscribe_logs=asyncio.Queue,
        recent_logs=list,
        unsubscribe_logs=lambda q: None,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=agent.app), base_url="http://test"
    ) as c:
        minted = (
            await c.post(
                "/api/v1/logs/token", headers={"Authorization": f"Bearer {ADMIN_KEY}"}
            )
        ).json()["sse_token"]

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(c.get(f"/api/v1/logs?sse_token={minted}"), 1.0)
