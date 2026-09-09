"""Documentation claims that a test can hold to the code.

Not every sentence is checkable, but the ones that cost a reader something are:
a config key that does not exist, a deployment recipe missing the volume that
holds the only irreplaceable state, a spec whose status says it shipped.

Each assertion here corresponds to something that was wrong at 1.34.0.
"""

import os
import re

import pytest

yaml = pytest.importorskip("yaml")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(path: str) -> str:
    with open(os.path.join(_REPO_ROOT, path)) as f:
        return f.read()


# ── the deployment recipe keeps what must survive ───────────────────────────


def test_the_docker_run_recipe_mounts_the_data_volume():
    """It mounted config and plugins and nothing at /app/data, so following it
    destroyed the registry, the budget, the ledger and the audit chain on the
    next container replacement — three sections above a Backups block that
    opens by calling data/ the only state that cannot be reconstructed."""
    guide = _read("docs/guide/deployment.md")
    recipe = guide[guide.index("## Docker Build") : guide.index("## Environment Variables")]

    assert "/app/data" in recipe, "the docker run recipe keeps no durable state"


def test_the_docker_run_recipe_leaves_the_installed_plugin_dir_writable():
    """-v ./plugins:/app/plugins:ro makes POST /api/v1/plugins/install fail."""
    guide = _read("docs/guide/deployment.md")
    recipe = guide[guide.index("## Docker Build") : guide.index("## Environment Variables")]

    assert "/app/plugins/bundled:ro" in recipe
    assert ":/app/plugins:ro" not in recipe, (
        "mounting the whole plugin tree read-only breaks plugin install"
    )


def test_the_deployment_guide_documents_rolling_back():
    """The word did not appear in either operational document, and the store
    runs migrations — so 'can the old binary open this database' was a question
    an operator had to answer under pressure with no help."""
    guide = _read("docs/guide/deployment.md")

    assert "rolling back" in guide.lower() or "rollback" in guide.lower()
    assert "helm rollback" in guide


# ── documented config keys exist, with the defaults the code uses ───────────


def _yaml_blocks(markdown: str) -> list:
    return [
        yaml.safe_load(block)
        for block in re.findall(r"```yaml\n(.*?)```", markdown, re.S)
        if block.strip()
    ]


def _flatten(node, prefix=""):
    if isinstance(node, dict):
        for k, v in node.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            yield path, v
            yield from _flatten(v, path)


@pytest.mark.parametrize(
    "path",
    [
        "server.auth.admin_keys_env",
        "server.total_timeout",
        "server.metrics.bind",
        "security.max_nesting_depth",
        "connection_pool.max_connections",
        "circuit_breaker.failure_threshold",
        "security.threat_ledger.threshold",
        "gdpr.retention_days",
    ],
)
def test_the_reference_documents_the_key(path):
    """Four whole sections — connection_pool, circuit_breaker, threat_ledger,
    gdpr — appeared in neither configuration document, along with the key that
    governs the request timeout budget and the one gating the control plane."""
    documented = {}
    for block in _yaml_blocks(_read("docs/reference/config.md")):
        documented.update(dict(_flatten(block)))

    assert path in documented, f"{path} is documented nowhere"


@pytest.mark.parametrize(
    "doc_path,code_default",
    [
        ("connection_pool.max_connections", 100),
        ("connection_pool.max_per_host", 30),
        ("connection_pool.connect_timeout", 10),
        ("connection_pool.dns_cache_ttl", 300),
        ("circuit_breaker.failure_threshold", 5),
        ("circuit_breaker.recovery_timeout", 60),
        ("security.threat_ledger.threshold", 3.0),
        ("security.threat_ledger.window_seconds", 600),
        ("security.threat_ledger.min_events", 3),
        ("security.threat_ledger.max_actors", 50000),
        ("gdpr.retention_days", 90),
        ("security.max_nesting_depth", 64),
    ],
)
def test_the_documented_default_is_the_code_default(doc_path, code_default):
    """A documented default that disagrees with the code is worse than none —
    I got three of these wrong on the first pass by writing them from memory
    instead of reading the source."""
    documented = {}
    for block in _yaml_blocks(_read("docs/reference/config.md")):
        documented.update(dict(_flatten(block)))

    assert documented[doc_path] == code_default, (
        f"{doc_path} documented as {documented[doc_path]!r}, code uses {code_default!r}"
    )


def test_the_admin_key_variable_is_named_where_operators_read():
    """It gated the whole control plane and appeared in none of these."""
    for path in (
        ".env.example",
        "docs/guide/configuration.md",
        "docs/reference/config.md",
        "docs/guide/deployment.md",
    ):
        assert "LLM_PROXY_ADMIN_KEYS" in _read(path), f"{path} does not name it"


def test_precedence_is_written_down_somewhere():
    """Four layers resolve on top of each other and no document said so."""
    guide = _read("docs/guide/configuration.md")

    assert "recedence" in guide
    assert "LLM_PROXY_DEV_MODE" in guide


# ── the rotation runbook points at files that exist ─────────────────────────


def test_the_rotation_script_resolves_the_current_salt_path():
    """It hardcoded the location the salt moved away from in 1.34.0, so its
    whole warning block was skipped on any install created since."""
    script = _read("scripts/rotate_keys.sh")

    assert "data/.llmproxy_salt" in script
    assert "LLM_PROXY_SALT_PATH" in script


def test_client_key_rotation_has_an_overlap_window():
    """It overwrote the bag with a single key, so every existing consumer died
    at the restart — in a list that is comma-separated precisely so it need
    not."""
    script = _read("scripts/rotate_keys.sh")

    assert "--retire-old" in script
    assert "_prepend_api_key" in script


# ── the spec says what is true of it ────────────────────────────────────────


def test_the_mcp_spec_is_not_presented_as_shipped():
    """Status REVIEWED-1 against target 1.22.0, at 1.34.0, with no MCP code."""
    spec = _read("docs/specs/mcp-native.md")
    header = spec[: spec.index("> **Review 1 changelog")]

    assert "DEFERRED" in header
    assert "1.22.0" not in header.split("\n")[4], "the table still targets a shipped release"


# ── the README does not carry a number that drifts ──────────────────────────


def test_the_readme_does_not_restate_the_test_count():
    """The body said 1391 while the badge said 1510 — the one claim a reader
    can check in thirty seconds, and it was wrong."""
    readme = _read("README.md")
    body = readme[readme.index("## "):]

    assert not re.search(r"\b1391 tests\b", body)
    assert not re.search(r"\b\d{3,5} tests \(\d+ passing", body), (
        "the body restates a count that the badge already carries, and it drifts"
    )
