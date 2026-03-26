"""Tests for core.rbac.RBACManager."""

import pytest
from core.rbac import RBACManager


@pytest.fixture
def rbac(tmp_path):
    db_path = str(tmp_path / "test_rbac.db")
    return RBACManager(db_path)


def test_admin_has_all_permissions(rbac):
    assert rbac.check_permission(["admin"], "proxy:use") is True
    assert rbac.check_permission(["admin"], "registry:write") is True
    assert rbac.check_permission(["admin"], "users:manage") is True


def test_user_limited_permissions(rbac):
    assert rbac.check_permission(["user"], "proxy:use") is True
    assert rbac.check_permission(["user"], "proxy:toggle") is False
    assert rbac.check_permission(["user"], "plugins:manage") is False


def test_viewer_readonly(rbac):
    assert rbac.check_permission(["viewer"], "registry:read") is True
    assert rbac.check_permission(["viewer"], "logs:read") is True
    assert rbac.check_permission(["viewer"], "proxy:use") is False


def test_check_permission_multiple_roles(rbac):
    assert rbac.check_permission(["user", "operator"], "plugins:manage") is True


@pytest.mark.asyncio
async def test_quota_default_allow(rbac):
    assert await rbac.check_quota("unknown-key-xyz") is True


@pytest.mark.asyncio
async def test_quota_exceeded(rbac):
    # add_quota(api_key, team, budget)
    rbac.add_quota("team-a-key", "team-a", 10.0)
    await rbac.update_usage("team-a-key", 15.0)
    assert await rbac.check_quota("team-a-key") is False


def test_set_get_user_roles(rbac):
    # set_user_roles(subject, email, roles)
    rbac.set_user_roles("sub-123", "user@test.com", ["admin", "operator"])
    roles = rbac.get_user_roles("sub-123")
    assert set(roles) == {"admin", "operator"}
