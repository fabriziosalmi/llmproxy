"""The default "user" role is an API consumer, not a control-plane reader.

"user" is the role every directory user gets unless a mapping says otherwise. It
held registry:read and logs:read, so any account in the configured identity provider
could read every upstream URL (/registry), other users' audit rows (/audit), the
plugin list, the dashboard and the export status. Reading the control plane is what
"viewer" is for.
"""

import pytest

from core.control_plane_policy import required_permission
from core.rbac import DEFAULT_PERMISSIONS, RBACManager

READ_ROUTES = [
    "/api/v1/registry",
    "/api/v1/audit",
    "/api/v1/metrics/latency",
    "/api/v1/plugins",
    "/api/v1/dashboard/summary",
    "/api/v1/export/status",
    "/api/v1/logs",
]


@pytest.fixture
def rbac(tmp_path):
    return RBACManager(str(tmp_path / "q.db"))


@pytest.mark.parametrize("path", READ_ROUTES)
def test_the_default_user_role_cannot_read_the_control_plane(rbac, path):
    needed = required_permission("GET", path)

    assert rbac.check_permission(["user"], needed) is False, (path, needed)


@pytest.mark.parametrize("path", READ_ROUTES)
def test_viewer_still_can(rbac, path):
    assert rbac.check_permission(["viewer"], required_permission("GET", path)) is True


def test_the_user_role_keeps_what_an_api_consumer_needs():
    assert DEFAULT_PERMISSIONS["user"] == {"proxy:use", "chat:use"}


@pytest.mark.parametrize("path", READ_ROUTES)
def test_operator_and_admin_are_unchanged(rbac, path):
    needed = required_permission("GET", path)

    assert rbac.check_permission(["operator"], needed) is True
    assert rbac.check_permission(["admin"], needed) is True
