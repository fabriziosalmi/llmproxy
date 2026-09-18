"""Which permission a control-plane request needs.

Two defects meet here, and neither can be fixed alone.

The first: the global middleware verified an API key and nothing else, so both
JWT paths this codebase implements produced a verified identity that opened
nothing. An SSO user with roles ["admin"] got `authenticated: true` from
/api/v1/identity/me and 401 from every other control-plane route, and the
`server.admin_auth.oidc_enabled` branch in the admin routes was unreachable
because the middleware rejected the JWT before dispatch. The whole point of
putting an organisation's directory in front of the proxy was unavailable.

The second: the RBAC matrix declared fourteen permissions and exactly one,
`proxy:use`, was ever consulted — on the three data-plane routes. An operator
could assign someone `viewer` and reasonably believe that person could not
install a plugin, and nothing in the code made that true.

Fixing only the first would be a privilege escalation: any user of a
configured directory would become an administrator. So the middleware now
resolves a principal from whichever credential was presented and checks it
against the permission this module names for the route.

Compatibility is the design constraint. An admin API key resolves to the
`admin` role, which holds every permission, so key-authenticated deployments
see no behaviour change whatsoever — the checks below can only ever refuse a
JWT-authenticated caller whose roles are genuinely insufficient.
"""

from __future__ import annotations

from typing import Optional, Tuple

#: Permission required when no rule below matches.
#
# `users:manage` is held by `admin` alone, so an unmatched control-plane route
# is administrator-only. That keeps the deny-by-default property the middleware
# was built around: a route added later is closed to every lesser role until
# someone deliberately names it here.
DEFAULT_PERMISSION = "users:manage"

#: (path prefix, permission for reads, permission for writes).
#
# Ordered — the first matching prefix wins, so put longer prefixes first. Reads
# are GET and HEAD; everything else is a write. The vocabulary is the one
# core/rbac.py already declares; nothing new is invented here, because a
# permission no role holds is a permission that only ever denies.
_RULES: Tuple[Tuple[str, str, str], ...] = (
    ("/api/v1/registry", "registry:read", "registry:write"),
    ("/api/v1/endpoints", "registry:read", "registry:write"),
    ("/api/v1/logs", "logs:read", "logs:clear"),
    ("/api/v1/plugins", "registry:read", "plugins:manage"),
    ("/api/v1/config", "proxy:config", "proxy:config"),
    ("/api/v1/proxy", "registry:read", "proxy:toggle"),
    ("/api/v1/features", "registry:read", "features:toggle"),
    ("/api/v1/routing", "registry:read", "features:toggle"),
    ("/api/v1/rate-limit", "registry:read", "features:toggle"),
    ("/api/v1/cache", "logs:read", "features:toggle"),
    ("/api/v1/security", "logs:read", "features:toggle"),
    ("/api/v1/threats", "logs:read", "features:toggle"),
    ("/api/v1/audit", "logs:read", "users:manage"),
    ("/api/v1/metrics", "logs:read", "users:manage"),
    ("/api/v1/dashboard", "logs:read", "users:manage"),
    ("/api/v1/status", "logs:read", "users:manage"),
    ("/api/v1/health", "logs:read", "users:manage"),
    ("/api/v1/models", "registry:read", "registry:write"),
    ("/api/v1/export", "logs:read", "users:manage"),
    ("/api/v1/version", "logs:read", "logs:read"),
    ("/api/v1/identity", "logs:read", "users:manage"),
    # Deliberately administrator-only, reads included: these expose or change
    # who may do what, or destroy evidence.
    ("/api/v1/rbac", "users:manage", "users:manage"),
    ("/api/v1/gdpr", "users:manage", "users:manage"),
    ("/api/v1/webhooks", "users:manage", "users:manage"),
    ("/api/v1/budget", "budget:manage", "budget:manage"),
    ("/metrics", "logs:read", "users:manage"),
    ("/admin/", DEFAULT_PERMISSION, DEFAULT_PERMISSION),
)

_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def required_permission(method: str, path: str) -> str:
    """The permission a caller must hold to make this control-plane request.

    Unmatched paths return DEFAULT_PERMISSION, which only `admin` holds.
    """
    is_read = (method or "").upper() in _READ_METHODS
    for prefix, read_perm, write_perm in _RULES:
        if path == prefix or path.startswith(prefix.rstrip("/") + "/") or path == prefix.rstrip("/"):
            return read_perm if is_read else write_perm
    return DEFAULT_PERMISSION


def describe(method: str, path: str) -> Optional[str]:
    """Human-readable rule for a path, for logs and error messages."""
    perm = required_permission(method, path)
    return f"{method.upper()} {path} requires '{perm}'"
