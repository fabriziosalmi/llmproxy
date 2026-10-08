"""Dependencies point inward: core/, store/ and plugins/ do not import proxy/.

core/health_prober.py imported the provider adapter registry from proxy/ (lazily, so
it hid from a top-of-file read). It was the one upward edge in the graph; the
resolver is injected now. This keeps it that way, counting lazy imports too.
"""

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _imports(path: pathlib.Path):
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.lineno, node.module


def _offenders(package: str, forbidden: str):
    return [
        f"{path.relative_to(ROOT)}:{lineno} imports {module}"
        for path in sorted((ROOT / package).rglob("*.py"))
        for lineno, module in _imports(path)
        if module == forbidden or module.startswith(forbidden + ".")
    ]


def test_core_does_not_import_proxy():
    assert _offenders("core", "proxy") == []


def test_store_does_not_import_proxy():
    assert _offenders("store", "proxy") == []


def test_plugins_do_not_import_proxy_or_store():
    assert _offenders("plugins", "proxy") == []
    assert _offenders("plugins", "store") == []
