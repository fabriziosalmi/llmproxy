"""Claims in the README and guides that can be checked against the code are.

At 1.37.19 the README said "tests-1755 passing" (2157 were collected) in a static
badge it claimed CI updated, "immutable audit ledger" for a keyless hash chain its
own threat model says a database writer can rewrite, "Redis-backed distributed
ring pipeline" for a plugin engine with no Redis use, and the guides gave three
different layer counts and two different plugin counts. Numbers that cannot be
kept honest are gone; the ones left are tested.
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text()
DOCS = [ROOT / "README.md", ROOT / "docs" / "index.md", *sorted((ROOT / "docs" / "guide").glob("*.md"))]


def test_no_static_test_or_coverage_badge():
    assert not re.search(r"img\.shields\.io/badge/(tests|coverage)-", README), (
        "a number typed into a badge goes stale; use a badge that reads CI, or none"
    )


def test_the_audit_log_is_not_called_immutable():
    assert "immutable audit" not in README.lower()


def test_the_plugin_engine_is_not_called_distributed_or_redis_backed():
    assert "redis-backed distributed ring" not in README.lower()
    engine = (ROOT / "core" / "plugin_engine.py").read_text()
    assert not re.search(r"^\s*(import|from)\s+redis", engine, re.M), (
        "the engine uses Redis now; the README may say so"
    )


def test_every_stated_marketplace_plugin_count_is_the_real_one():
    real = len([p for p in (ROOT / "plugins" / "marketplace").glob("*.py") if p.name != "__init__.py"])
    stated = [
        (path.name, int(m.group(1)))
        for path in DOCS
        for m in re.finditer(r"(\d+) marketplace plugins", path.read_text())
    ]
    assert stated, "no plugin count is stated any more; delete this test"
    assert [s for s in stated if s[1] != real] == [], f"real count is {real}"


def test_no_guide_states_a_layer_count():
    for path in DOCS:
        assert not re.search(r"\b\d+[- ](security |defense )?layers?\b", path.read_text(), re.I), path.name
