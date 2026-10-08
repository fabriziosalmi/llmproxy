"""Every route the app registers is documented under docs/api/.

Counting the route decorators against the docs found 30 control-plane paths
that no page mentioned, among them /api/v1/gdpr/purge, /api/v1/security/reset,
/api/v1/firewall/reset and /api/v1/cache/clear: routes that delete or reset
state, with no stated permission, effect or contract. The schema is read from
the app create_app actually builds, so a route added anywhere shows up here.
"""

import glob
import os
import re

import pytest
from conftest import InMemoryRepository, minimal_config
from test_e2e import LightweightAgent

DOCS = os.path.join(os.path.dirname(__file__), "..", "docs", "api", "*.md")

# Paths that are served but deliberately not part of the documented API.
EXEMPT: set[str] = set()


def _normalise(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "{}", path)


def _documented_paths() -> set[str]:
    text = "".join(open(f).read() for f in glob.glob(DOCS))
    return {
        _normalise(p)
        for p in re.findall(r"(/(?:api/v1|v1|health|ready|metrics)[A-Za-z0-9_/{}.\-]*)", text)
    }


@pytest.fixture(scope="module")
def registered_paths():
    from proxy.app_factory import create_app

    config = minimal_config()
    config["server"]["auth"]["enabled"] = False
    app = create_app(LightweightAgent(InMemoryRepository(), config))
    return sorted(app.openapi()["paths"])


def test_every_registered_path_is_documented(registered_paths):
    documented = _documented_paths()
    missing = [
        p
        for p in registered_paths
        if _normalise(p) not in documented and p not in EXEMPT
    ]
    assert not missing, (
        "routes with no mention in docs/api/*.md (document them, or add them to "
        "EXEMPT with a reason):\n  " + "\n  ".join(missing)
    )


def test_the_destructive_routes_are_documented_first_class():
    """The ones that delete or reset state carry an explicit warning."""
    text = "".join(open(f).read() for f in glob.glob(DOCS))
    for path in (
        "/api/v1/gdpr/purge",
        "/api/v1/gdpr/erase/{subject}",
        "/api/v1/security/reset",
        "/api/v1/firewall/reset",
        "/api/v1/cache/clear",
    ):
        assert path in text, f"{path} is not documented"
    assert text.count("**Destructive.**") >= 4


def test_the_data_plane_error_shape_is_documented():
    text = open(os.path.join(os.path.dirname(DOCS), "proxy.md")).read()
    assert "## Errors" in text
    for needle in ("error.type", "error.code", "authentication_error", "detail"):
        assert needle in text
