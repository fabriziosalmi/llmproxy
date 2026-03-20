"""Tests for core.identity.IdentityManager."""

import pytest
import time
import asyncio
from unittest.mock import patch, MagicMock
from dataclasses import dataclass

IDENTITY_CONFIG = {
    "identity": {
        "enabled": True,
        "default_role": "user",
        "providers": [],
        "role_mappings": {"admin@test.com": ["admin", "operator"]},
        "session_ttl": 3600,
    }
}

DISABLED_CONFIG = {"identity": {"enabled": False}}


@pytest.mark.asyncio
@patch("core.identity.get_secret", return_value=None)
async def test_disabled_returns_none(mock_secret):
    from core.identity import IdentityManager
    mgr = IdentityManager(DISABLED_CONFIG)
    result = await mgr.verify_token("some-token")
    assert result is None


@pytest.mark.asyncio
@patch("core.identity.get_secret", return_value=None)
async def test_non_jwt_returns_none(mock_secret):
    from core.identity import IdentityManager
    mgr = IdentityManager(IDENTITY_CONFIG)
    result = await mgr.verify_token("not-a-jwt-token")
    assert result is None


@pytest.mark.asyncio
@patch("core.identity.get_secret", return_value=None)
async def test_malformed_jwt_returns_none(mock_secret):
    from core.identity import IdentityManager
    mgr = IdentityManager(IDENTITY_CONFIG)
    result = await mgr.verify_token("aaa.bbb.ccc")
    assert result is None


@patch("core.identity.get_secret", return_value="test-secret-key-1234567890")
def test_generate_and_verify_proxy_jwt(mock_secret):
    from core.identity import IdentityManager, IdentityContext
    mgr = IdentityManager(IDENTITY_CONFIG)
    identity = IdentityContext(
        provider="google",
        subject="user1",
        email="user1@test.com",
        name="Test User",
        roles=["user"],
        verified=True,
    )
    token = mgr.generate_proxy_jwt(identity, ttl=3600)
    assert token is not None
    assert isinstance(token, str)

    ctx = mgr.verify_proxy_jwt(token)
    assert ctx is not None
    assert ctx.subject == "user1"
    assert ctx.email == "user1@test.com"
    assert "user" in ctx.roles


@patch("core.identity.get_secret", return_value="test-secret-key-1234567890")
def test_proxy_jwt_expired(mock_secret):
    from core.identity import IdentityManager, IdentityContext
    mgr = IdentityManager(IDENTITY_CONFIG)
    identity = IdentityContext(
        provider="google", subject="user1", email="user1@test.com",
        roles=["user"], verified=True,
    )
    token = mgr.generate_proxy_jwt(identity, ttl=0)
    time.sleep(0.1)
    ctx = mgr.verify_proxy_jwt(token)
    assert ctx is None


@patch("core.identity.get_secret", return_value=None)
def test_resolve_roles_from_mapping(mock_secret):
    from core.identity import IdentityManager, OIDCProvider
    mgr = IdentityManager(IDENTITY_CONFIG)
    # _resolve_roles(claims, provider, email)
    fake_provider = OIDCProvider(
        name="google", issuer="https://accounts.google.com",
        jwks_uri="", client_id="test",
    )
    roles = mgr._resolve_roles({}, fake_provider, "admin@test.com")
    assert "admin" in roles
    assert "operator" in roles


@patch("core.identity.get_secret", return_value=None)
def test_resolve_roles_default(mock_secret):
    from core.identity import IdentityManager, OIDCProvider
    mgr = IdentityManager(IDENTITY_CONFIG)
    fake_provider = OIDCProvider(
        name="google", issuer="https://accounts.google.com",
        jwks_uri="", client_id="test",
    )
    roles = mgr._resolve_roles({}, fake_provider, "nobody@test.com")
    assert roles == ["user"]
