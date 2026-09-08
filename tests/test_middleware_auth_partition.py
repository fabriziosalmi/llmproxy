"""The protected/public partition of the admin middleware, asserted as a table.

proxy/app_factory.py declares three collections that decide, before any
handler runs, whether a request needs a credential: _PROTECTED_PREFIXES,
_PUBLIC_EXACT and _ALSO_PROTECT. The module docstring states that only paths
in _PUBLIC_EXACT are reachable without credentials.

That claim was false. /v1/models sits outside the protected prefixes and its
handler performed no check of its own, so it served the configured provider
and model inventory to anyone who could reach the port while every /api/v1/
sibling returned 401. The middleware was at 50% statement coverage; nothing
exercised the partition it defines.

This is the table-driven test that would have caught it, and that will catch
the next route added outside the partition.
"""

import pytest

from proxy import app_factory


def _needs_auth(path: str) -> bool:
    """Exactly the middleware's decision, read from the real collections."""
    protected = any(path.startswith(p) for p in app_factory._PROTECTED_PREFIXES)
    also = path in app_factory._ALSO_PROTECT
    return (protected or also) and path not in app_factory._PUBLIC_EXACT


# ── what the collections must contain ───────────────────────────────────────


def test_the_control_plane_prefix_is_protected():
    assert "/api/v1/" in app_factory._PROTECTED_PREFIXES


def test_metrics_is_protected_despite_sitting_outside_the_prefixes():
    """Token counts and timings are a cross-tenant side channel."""
    assert "/metrics" in app_factory._ALSO_PROTECT
    assert _needs_auth("/metrics")


def test_health_is_public_so_probes_work_without_credentials():
    assert "/health" in app_factory._PUBLIC_EXACT
    assert not _needs_auth("/health")


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/identity/config",
        "/api/v1/identity/exchange",
        "/api/v1/identity/me",
    ],
)
def test_the_identity_bootstrap_endpoints_are_deliberately_public(path):
    """Each validates its own token, or answers 'not authenticated'."""
    assert path in app_factory._PUBLIC_EXACT
    assert not _needs_auth(path)


# ── the control plane is closed by default ──────────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/version",
        "/api/v1/registry",
        "/api/v1/config/raw",
        "/api/v1/config/apply",
        "/api/v1/plugins",
        "/api/v1/audit",
        "/api/v1/gdpr/retention",
        "/api/v1/dashboard/summary",
        "/api/v1/rbac/roles",
        "/api/v1/webhooks",
        "/api/v1/metrics/latency",
        "/api/v1/a-route-nobody-has-written-yet",
    ],
)
def test_every_control_plane_path_requires_a_credential(path):
    """Including one that does not exist: the prefix closes by default."""
    assert _needs_auth(path), f"{path} is reachable without a credential"


# ── the data plane is not covered here, and that is deliberate ──────────────


@pytest.mark.parametrize(
    "path",
    ["/v1/chat/completions", "/v1/completions", "/v1/embeddings", "/v1/models"],
)
def test_the_data_plane_is_outside_the_middleware(path):
    """Not an oversight: the middleware only verifies API keys.

    The data plane also accepts JWTs, so covering /v1/ here would reject valid
    callers. Each handler authenticates instead — which is exactly why a
    handler that forgets is invisible to the middleware, as /v1/models was.
    tests/test_data_plane_auth.py holds those handlers to it.
    """
    assert not _needs_auth(path)


def test_every_data_plane_route_module_authenticates_itself():
    """The obligation the previous test creates, checked at source level.

    If /v1/ is outside the middleware, each route module serving it must
    perform its own check. /v1/models did not, and nothing noticed.
    """
    import pathlib

    repo = pathlib.Path(__file__).resolve().parent.parent
    offenders = []
    for name in ("chat.py", "completions.py", "embeddings.py", "models.py"):
        source = (repo / "proxy" / "routes" / name).read_text()
        if not any(
            marker in source
            for marker in ("require_data_plane_auth", "_verify_api_key", "API_KEY_HEADER")
        ):
            offenders.append(name)
    assert not offenders, (
        f"these /v1/ route modules perform no authentication and are not "
        f"covered by the middleware: {offenders}"
    )


# ── the query-token exception stays narrow ──────────────────────────────────


def test_the_query_token_fallback_is_limited_to_the_sse_stream():
    """A query-string credential is acceptable only where headers cannot be set."""
    assert app_factory._QUERY_TOKEN_FALLBACK_PATHS == frozenset({"/api/v1/logs"}), (
        "widening the query-token fallback puts credentials in URLs, which end "
        "up in proxy logs and browser history"
    )
