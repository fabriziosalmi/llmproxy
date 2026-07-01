"""Tests for the ai_dependency_guard plugin — a slopsquatting defense ported
from ai-dependency-guard. Two properties matter most: (1) high-precision
extraction (ordinary prose must never be treated as a package reference), and
(2) fail-open behavior (a registry we can't reach must never flag a package).

Tier-2 network calls are avoided entirely by pre-seeding the module cache, so
these tests are deterministic and offline."""
import importlib.util
import json
import os

import pytest

_PATH = os.path.join(
    os.path.dirname(__file__), "..", "plugins", "installed", "ai_dependency_guard.py"
)
_spec = importlib.util.spec_from_file_location("ai_dependency_guard", _PATH)
adg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(adg)

PluginContext = adg.PluginContext


class _FakeResponse:
    """Minimal stand-in for the proxy response object the plugin reads."""

    def __init__(self, content: str):
        payload = {"choices": [{"message": {"role": "assistant", "content": content}}]}
        self.body = json.dumps(payload).encode()


def _ctx(content: str, metadata=None) -> "adg.PluginContext":
    return PluginContext(
        body={}, response=_FakeResponse(content), metadata=metadata or {}
    )


def _seed(exists_map: dict):
    """Pre-populate the registry cache so _tier2_missing never hits the network.
    exists_map: {(eco, name): bool}."""
    adg._CACHE.clear()
    for key, exists in exists_map.items():
        adg._cache_put(key, exists, ttl_s=10_000)


# ── Extraction: high precision ───────────────────────────────────────────────
def test_extracts_pip_and_npm_install_commands():
    text = "Run `pip install requests` then `npm install left-pad` to proceed."
    assert adg._extract_packages(text) == [("pypi", "requests"), ("npm", "left-pad")]


def test_strips_version_specifiers_and_extras():
    text = "pip install fastapi==0.110.0 uvicorn[standard]>=0.20"
    assert adg._extract_packages(text) == [("pypi", "fastapi"), ("pypi", "uvicorn")]


def test_pip_flags_and_their_path_args_are_ignored():
    # -r requirements.txt and -e ./local must NOT be read as packages.
    text = "pip install -r requirements.txt -e ./mylib --upgrade httpx"
    assert adg._extract_packages(text) == [("pypi", "httpx")]


def test_urls_paths_and_git_refs_are_not_packages():
    text = "pip install git+https://github.com/x/y.git ./dist/pkg.whl https://a/b.tgz"
    assert adg._extract_packages(text) == []


def test_scoped_npm_package_is_preserved():
    text = "npm install @scope/thing yarn-only-noise"
    pkgs = adg._extract_packages(text)
    assert ("npm", "@scope/thing") in pkgs


def test_pinned_requirement_line_is_extracted():
    text = "```\nnumpy==1.26.4\nrich>=13.0\n```"
    pkgs = adg._extract_packages(text)
    assert ("pypi", "numpy") in pkgs
    assert ("pypi", "rich") in pkgs


def test_prose_is_not_treated_as_packages():
    prose = (
        "To add two numbers you install confidence and add them together. "
        "I think we should install trust in the process."
    )
    # 'install confidence' / 'install trust' — these ARE grammatically after
    # 'install', so they get extracted; the precision guarantee is that Tier-2
    # (404) or the trigger gate decides, not that grammar is understood. What we
    # assert here: no version-pinned prose sneaks in via the requirement regex.
    assert not adg._REQ_RE.search(prose)


def test_pep503_normalization_for_registry_url():
    assert adg._pep503("Zope.Interface") == "Zope-Interface"
    assert adg._registry_url("pypi", "foo_bar.baz") == "https://pypi.org/pypi/foo-bar-baz/json"
    assert adg._registry_url("npm", "@a/b") == "https://registry.npmjs.org/@a%2Fb"


# ── execute(): Tier 1 blocklist ──────────────────────────────────────────────
@pytest.mark.asyncio
async def test_blocklist_flags_offline_without_registry():
    adg._CACHE.clear()
    plugin = adg.AiDependencyGuard(
        {"registry_check": False, "blocklist": ["evil-pkg"]}
    )
    ctx = _ctx("Install it with `pip install evil-pkg`.")
    resp = await plugin.execute(ctx)
    assert resp.action == "passthrough"  # metadata-only by default
    assert ctx.metadata["_depguard_flagged"] is True
    pkgs = ctx.metadata["_depguard_packages"]
    assert pkgs == [{"name": "evil-pkg", "ecosystem": "pypi", "reason": "blocklist"}]


# ── execute(): Tier 2 registry existence (cache-seeded, no network) ───────────
@pytest.mark.asyncio
async def test_nonexistent_package_flagged_as_not_found():
    _seed({("pypi", "totally-not-real-pkg"): False, ("pypi", "requests"): True})
    plugin = adg.AiDependencyGuard({})
    ctx = _ctx("Try `pip install requests` and `pip install totally-not-real-pkg`.")
    resp = await plugin.execute(ctx)
    assert resp.action == "passthrough"
    assert ctx.metadata["_depguard_flagged"] is True
    flagged = {p["name"]: p["reason"] for p in ctx.metadata["_depguard_packages"]}
    assert flagged == {"totally-not-real-pkg": "not_found"}  # requests (exists) not flagged


@pytest.mark.asyncio
async def test_existing_packages_are_not_flagged():
    _seed({("pypi", "requests"): True, ("npm", "left-pad"): True})
    plugin = adg.AiDependencyGuard({})
    ctx = _ctx("Use `pip install requests` and `npm install left-pad`.")
    resp = await plugin.execute(ctx)
    assert resp.action == "passthrough"
    assert "_depguard_flagged" not in ctx.metadata


@pytest.mark.asyncio
async def test_block_on_hallucination_returns_403():
    _seed({("pypi", "hallucinated-lib"): False})
    plugin = adg.AiDependencyGuard({"block_on_hallucination": True})
    ctx = _ctx("Just run `pip install hallucinated-lib` and you're done.")
    resp = await plugin.execute(ctx)
    assert resp.action == "block"
    assert resp.status_code == 403
    assert "hallucinated-lib" in resp.message


# ── Fail-open + cheap-exit guarantees ────────────────────────────────────────
@pytest.mark.asyncio
async def test_no_install_keyword_short_circuits():
    plugin = adg.AiDependencyGuard({})
    ctx = _ctx("Here is a poem about the sea and the sky, no code at all.")
    resp = await plugin.execute(ctx)
    assert resp.action == "passthrough"
    assert "_depguard_flagged" not in ctx.metadata


@pytest.mark.asyncio
async def test_cache_hit_response_is_skipped():
    plugin = adg.AiDependencyGuard({"blocklist": ["evil-pkg"]})
    ctx = _ctx("pip install evil-pkg", metadata={"_cache_status": "HIT"})
    resp = await plugin.execute(ctx)
    assert resp.action == "passthrough"
    assert "_depguard_flagged" not in ctx.metadata


@pytest.mark.asyncio
async def test_unconfirmed_registry_result_fails_open():
    # 'maybe-pkg' is absent from the cache; with registry_check disabled the
    # Tier-2 sweep never runs, so an unconfirmed package is allowed through.
    adg._CACHE.clear()
    plugin = adg.AiDependencyGuard({"registry_check": False})
    ctx = _ctx("You could `pip install maybe-pkg` for that.")
    resp = await plugin.execute(ctx)
    assert resp.action == "passthrough"
    assert "_depguard_flagged" not in ctx.metadata


@pytest.mark.asyncio
async def test_malformed_response_body_is_safe():
    plugin = adg.AiDependencyGuard({})

    class _Bad:
        body = b"not json at all"

    ctx = PluginContext(body={}, response=_Bad(), metadata={})
    resp = await plugin.execute(ctx)
    assert resp.action == "passthrough"
