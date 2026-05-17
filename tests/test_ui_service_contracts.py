"""
Static validation that every UI service module exposes the API the rest of
the UI imports. This is a cheap tripwire that catches a refactor that
renames / removes an export without updating its consumers.

No JS runtime: regex checks on file contents. We verify:
  1. Each service file contains the expected `export` declarations.
  2. Each expected consumer (main.js + component files) imports the
     symbols the service is advertised to provide.
  3. Cross-file symmetry: if X imports `foo` from './services/y.js', then
     'y.js' exports `foo`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

UI_ROOT = Path(__file__).parent.parent / "ui"

# ── Expected exports per service (what the rest of the UI relies on) ──────

EXPECTED_EXPORTS = {
    # Legacy JS services that still front the data layer (api/store/toast/timerange/auth).
    "services/toast.js":     {"toast"},
    "services/store.js":     {"store"},
    "services/api.js":       {"api"},
    "services/auth.js":      {"auth"},
    "services/timerange.js": {"initTimerange", "timerange"},
    # J.2: explain + drilldown migrated to TS. Modal/dialog and Drawer
    # primitives moved to src/ui/ in Phase E. The four legacy files
    # (services/dialog.js, services/drawer.js, services/explain.js,
    # services/drilldown.js) were retired — assertions removed so this
    # tripwire stays accurate, not stale.
    "src/services/explain.ts":   {"initExplain", "explain", "markExplainable"},
    "src/services/drilldown.ts": {"initDrilldown", "drilldown"},
}

_EXPORT_RE = re.compile(
    r"""export\s+
        (?:
            (?:default\s+)?
            (?:async\s+)?
            (?:const|let|var|function|class)\s+
            (\w+)
          | \{\s*([^}]+)\s*\}
        )
    """,
    re.VERBOSE,
)


def _collect_exports(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    names: set[str] = set()
    for m in _EXPORT_RE.finditer(text):
        if m.group(1):
            names.add(m.group(1))
        elif m.group(2):
            # `export { a, b as c }` — alias 'a as c' exports 'c'.
            for item in m.group(2).split(","):
                token = item.strip()
                if not token:
                    continue
                if " as " in token:
                    token = token.split(" as ")[-1].strip()
                names.add(token)
    return names


@pytest.mark.parametrize("rel,expected", sorted(EXPECTED_EXPORTS.items()))
def test_service_exports(rel, expected):
    path = UI_ROOT / rel
    assert path.exists(), f"Service file missing: {path}"
    exports = _collect_exports(path)
    missing = expected - exports
    assert not missing, (
        f"{rel} is missing expected exports: {missing}. "
        f"Found: {sorted(exports)}"
    )


# ── Consumer cross-check: required imports resolve ────────────────────────

_IMPORT_RE = re.compile(
    r"""import\s+
        (?:
            (\w+)
          | \*\s+as\s+(\w+)
          | \{\s*([^}]+)\s*\}
        )
        \s+from\s+['"]([^'"]+)['"]
    """,
    re.VERBOSE,
)


def _collect_imports(path: Path) -> list[tuple[str, str]]:
    """Return list of (imported_symbol, source_path)."""
    text = path.read_text(encoding="utf-8")
    out: list[tuple[str, str]] = []
    for m in _IMPORT_RE.finditer(text):
        default_imp, star_as, named_list, source = m.groups()
        if default_imp:
            out.append((default_imp, source))
        if star_as:
            out.append((star_as, source))
        if named_list:
            for item in named_list.split(","):
                token = item.strip()
                if not token:
                    continue
                if " as " in token:
                    token = token.split(" as ")[0].strip()
                out.append((token, source))
    return out


_CONSUMER_FILES = [
    "main.js",
    "chat.js",
    *[str(p.relative_to(UI_ROOT)) for p in (UI_ROOT / "components").glob("*.js")],
]


def test_all_ui_imports_resolve():
    """Every './services/X.js' import in the UI must correspond to an export
    of that service file. Non-services imports (components, vendors) are
    skipped — this test is targeted at service-level coupling."""
    unresolved: list[str] = []
    for rel in _CONSUMER_FILES:
        path = UI_ROOT / rel
        if not path.exists():
            continue
        for symbol, source in _collect_imports(path):
            # Only check imports that target UI services (relative path containing /services/).
            if "/services/" not in source:
                continue
            service_rel = source.replace("../", "").replace("./", "")
            if not service_rel.startswith("services/"):
                continue
            # Drop query / hash and normalize.
            service_rel = service_rel.split("?", 1)[0].split("#", 1)[0]
            service_path = UI_ROOT / service_rel
            if not service_path.exists():
                unresolved.append(f"{rel} imports from non-existent {source}")
                continue
            exports = _collect_exports(service_path)
            if symbol not in exports:
                unresolved.append(
                    f"{rel} imports '{symbol}' from {source}, but service only exports {sorted(exports)}"
                )
    assert not unresolved, "Cross-file export/import mismatch:\n  " + "\n  ".join(unresolved)


# ── Wiring spot-check: data-explain / data-drilldown attributes are present ─


def test_data_explain_is_wired():
    """Key surfaces must carry data-explain so the trust-by-explanation
    drawer opens when the user clicks the badge."""
    guards = (UI_ROOT / "components/guards.js").read_text()
    registry = (UI_ROOT / "components/registry.js").read_text()

    assert 'data-explain="firewall"' in guards or "data-explain='firewall'" in guards \
        or "'firewall'" in guards and "data-explain" in guards, \
        "ASGI firewall card must carry data-explain='firewall'"
    assert "data-explain" in guards and "guard:" in guards, \
        "Per-guard cards must carry data-explain='guard:<name>'"
    assert "data-explain" in registry and "circuit:" in registry, \
        "Registry circuit column must carry data-explain='circuit:<id>'"


def test_data_drilldown_is_wired():
    """Entry points that open drilldown: registry (Inspect), audit rows,
    models table row, plugins card Inspect."""
    registry = (UI_ROOT / "components/registry.js").read_text()
    # Security view was migrated to TypeScript; drilldown wiring for audit rows
    # now lives in src/views/security/AuditResultsTable.ts.
    security = (UI_ROOT / "src/views/security/AuditResultsTable.ts").read_text()
    models = (UI_ROOT / "components/models.js").read_text()
    plugins = (UI_ROOT / "components/plugins.js").read_text()

    assert "data-drilldown" in registry and "endpoint:" in registry, \
        "Registry must expose data-drilldown='endpoint:<id>'"
    assert ("data-drilldown" in security or "dataset.drilldown" in security) and "request:" in security, \
        "Audit table rows must carry data-drilldown/dataset.drilldown='request:<req_id>'"
    assert "data-drilldown" in models and "model:" in models, \
        "Models table rows must carry data-drilldown='model:<id>'"
    assert "data-drilldown" in plugins and "plugin:" in plugins, \
        "Plugin cards must expose data-drilldown='plugin:<name>'"
