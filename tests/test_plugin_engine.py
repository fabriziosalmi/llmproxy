"""Tests for core.plugin_engine.ast_scan and PluginSecurityError."""

import hashlib

import pytest

from core.plugin_engine import PluginSecurityError, ast_scan, compute_plugin_sha256


def test_safe_code_passes():
    code = "import json\nimport re\nx = json.dumps({'a': 1})"
    assert ast_scan(code, "test_plugin") is True


def test_forbidden_os_import():
    with pytest.raises(PluginSecurityError):
        ast_scan("import os", "bad_plugin")


def test_forbidden_subprocess():
    with pytest.raises(PluginSecurityError):
        ast_scan("import subprocess", "bad_plugin")


def test_forbidden_exec_call():
    with pytest.raises(PluginSecurityError):
        ast_scan("exec('print(1)')", "bad_plugin")


def test_forbidden_eval_call():
    with pytest.raises(PluginSecurityError):
        ast_scan("eval('1+1')", "bad_plugin")


def test_forbidden_import_from_os():
    with pytest.raises(PluginSecurityError):
        ast_scan("from os import path", "bad_plugin")


def test_allowed_modules_pass():
    code = "import json\nimport re\nimport math"
    assert ast_scan(code, "safe_plugin") is True


def test_syntax_error_raises():
    with pytest.raises(PluginSecurityError):
        ast_scan("def (", "broken_plugin")


# ── M.1 — SHA-256 plugin pinning ──────────────────────────────────


def test_compute_plugin_sha256_matches_hashlib():
    """Helper is a thin wrapper around hashlib.sha256 over UTF-8 bytes."""
    src = "def hello():\n    return 'world'\n"
    expected = hashlib.sha256(src.encode("utf-8")).hexdigest()
    assert compute_plugin_sha256(src) == expected


def test_compute_plugin_sha256_unicode_stable():
    """Non-ASCII content hashes deterministically via UTF-8."""
    src = "# autore: Fabrìzio Salmî\nx = 'caffè'\n"
    h1 = compute_plugin_sha256(src)
    h2 = compute_plugin_sha256(src)
    assert h1 == h2
    assert len(h1) == 64  # sha256 hex


def test_compute_plugin_sha256_changes_with_whitespace():
    """Trailing whitespace shifts the hash — that's the point: any bit-level
    change in the file on disk breaks the pin."""
    base = "x = 1\n"
    tampered = "x = 1\n "  # one extra space after newline
    assert compute_plugin_sha256(base) != compute_plugin_sha256(tampered)


# ── Integration: install records pin, load verifies it ────────────


def _write_plugin_file(plugins_dir, name, source):
    """Write a plugin source file under plugins_dir/<name>.py and return the path."""
    import os

    path = os.path.join(plugins_dir, f"{name}.py")
    with open(path, "w") as f:
        f.write(source)
    return path


_VALID_PLUGIN_SRC = '''
"""Trivial test plugin — empty execute()."""
async def execute(ctx):
    return None
'''


@pytest.mark.asyncio
async def test_install_records_sha256_for_python_plugin(tmp_path):
    """install_plugin must hash the entrypoint file and stamp manifest_entry."""
    from core.plugin_engine import PluginManager

    plugins_dir = str(tmp_path)
    _write_plugin_file(plugins_dir, "trivial", _VALID_PLUGIN_SRC)
    # Bundled manifest is required for hot_swap → load_plugins() to succeed.
    import yaml as _yaml

    with open(tmp_path / "manifest.yaml", "w") as f:
        _yaml.safe_dump({"plugins": []}, f)

    pm = PluginManager(plugins_dir=plugins_dir)
    entry = {
        "name": "trivial",
        "hook": "pre_flight",
        "type": "python",
        "entrypoint": "trivial:execute",
        "enabled": False,  # don't actually try to wire it during hot_swap
    }
    await pm.install_plugin(entry)

    # The dict the caller passed in is mutated in-place with the hash.
    assert "sha256" in entry
    assert entry["sha256"] == compute_plugin_sha256(_VALID_PLUGIN_SRC)
    # And the persisted manifest carries it.
    with open(tmp_path / "installed" / "manifest.yaml") as f:
        persisted = _yaml.safe_load(f)
    assert persisted["plugins"][0]["sha256"] == entry["sha256"]


@pytest.mark.asyncio
async def test_install_preserves_externally_supplied_sha256(tmp_path):
    """A caller (e.g. a marketplace publish step) can pre-pin the hash;
    install must not overwrite it."""
    from core.plugin_engine import PluginManager

    plugins_dir = str(tmp_path)
    _write_plugin_file(plugins_dir, "preset", _VALID_PLUGIN_SRC)
    import yaml as _yaml

    with open(tmp_path / "manifest.yaml", "w") as f:
        _yaml.safe_dump({"plugins": []}, f)

    pm = PluginManager(plugins_dir=plugins_dir)
    fake_hash = "deadbeef" * 8  # 64 hex chars
    entry = {
        "name": "preset",
        "hook": "pre_flight",
        "type": "python",
        "entrypoint": "preset:execute",
        "enabled": False,
        "sha256": fake_hash,
    }
    await pm.install_plugin(entry)
    assert entry["sha256"] == fake_hash


@pytest.mark.asyncio
async def test_load_rejects_tampered_file(tmp_path):
    """If sha256 is recorded but the file on disk has changed, load must fail."""
    from core.plugin_engine import PluginManager, PluginSecurityError

    plugins_dir = str(tmp_path)
    file_path = _write_plugin_file(plugins_dir, "tampered", _VALID_PLUGIN_SRC)
    import yaml as _yaml

    with open(tmp_path / "manifest.yaml", "w") as f:
        _yaml.safe_dump({"plugins": []}, f)

    pm = PluginManager(plugins_dir=plugins_dir)
    p_info = {
        "name": "tampered",
        "hook": "pre_flight",
        "type": "python",
        "entrypoint": "tampered:execute",
        "sha256": compute_plugin_sha256(_VALID_PLUGIN_SRC),
    }

    # Mutate the file AFTER pinning — same shape, bit-different bytes.
    with open(file_path, "w") as f:
        f.write(_VALID_PLUGIN_SRC + "# tampered\n")

    with pytest.raises(PluginSecurityError) as excinfo:
        await pm._load_plugin(p_info)
    assert "SHA-256 pin mismatch" in str(excinfo.value)


@pytest.mark.asyncio
async def test_load_without_pin_warns_but_succeeds(tmp_path, caplog):
    """No recorded hash → log a warning, don't block. Bundled plugins from a
    vendor manifest that hasn't been re-pinned still work."""
    from core.plugin_engine import PluginManager

    plugins_dir = str(tmp_path)
    _write_plugin_file(plugins_dir, "unpinned", _VALID_PLUGIN_SRC)
    import yaml as _yaml

    with open(tmp_path / "manifest.yaml", "w") as f:
        _yaml.safe_dump({"plugins": []}, f)

    pm = PluginManager(plugins_dir=plugins_dir)
    p_info = {
        "name": "unpinned",
        "hook": "pre_flight",
        "type": "python",
        "entrypoint": "unpinned:execute",
    }
    # No sha256 in p_info — should still load.
    import logging

    with caplog.at_level(logging.WARNING, logger="plugin_engine"):
        await pm._load_plugin(p_info)
    warns = [r.message for r in caplog.records if "SHA-256 pin" in r.message]
    assert len(warns) == 1


# ── P0-4: Atomic hot-swap ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_hot_swap_build_failure_preserves_live_state(tmp_path):
    """If _build_plugin_state raises, self.* must be untouched. The previous
    state stays live — no fail-open window where rings are empty or partial."""
    import yaml as _yaml

    from core.plugin_engine import PluginHook, PluginManager

    plugins_dir = str(tmp_path)
    _write_plugin_file(plugins_dir, "p1", _VALID_PLUGIN_SRC)
    with open(tmp_path / "manifest.yaml", "w") as f:
        _yaml.safe_dump(
            {
                "plugins": [
                    {
                        "name": "p1",
                        "hook": "pre_flight",
                        "type": "python",
                        "entrypoint": "p1:execute",
                    },
                ]
            },
            f,
        )

    pm = PluginManager(plugins_dir=plugins_dir)
    await pm.load_plugins()
    # Sanity: p1 is loaded
    assert any(p["name"] == "p1" for p in pm.rings[PluginHook.PRE_FLIGHT])
    snap_rings = pm.rings
    snap_meta = pm._plugin_meta
    snap_instances = pm._plugin_instances
    snap_stats = pm._plugin_stats

    # Force the build path to blow up.
    async def boom(**_):
        raise RuntimeError("synthetic build failure")

    pm._build_plugin_state = boom  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="synthetic build failure"):
        await pm.hot_swap()

    # Pointers must be IDENTICAL (not just equal) — no swap occurred.
    assert pm.rings is snap_rings
    assert pm._plugin_meta is snap_meta
    assert pm._plugin_instances is snap_instances
    assert pm._plugin_stats is snap_stats
    # And p1 is still live
    assert any(p["name"] == "p1" for p in pm.rings[PluginHook.PRE_FLIGHT])


@pytest.mark.asyncio
async def test_hot_swap_no_partial_clear_window(tmp_path):
    """Before the fix, load_plugins() cleared self.rings BEFORE rebuilding,
    creating a window where concurrent execute_ring observed empty rings.
    Now hot_swap builds new state off-side and swaps in one block — at no
    point should rings be empty if they were non-empty before AND a valid
    new state was built."""
    import yaml as _yaml

    from core.plugin_engine import PluginHook, PluginManager

    plugins_dir = str(tmp_path)
    _write_plugin_file(plugins_dir, "p1", _VALID_PLUGIN_SRC)
    with open(tmp_path / "manifest.yaml", "w") as f:
        _yaml.safe_dump(
            {
                "plugins": [
                    {
                        "name": "p1",
                        "hook": "pre_flight",
                        "type": "python",
                        "entrypoint": "p1:execute",
                    },
                ]
            },
            f,
        )
    pm = PluginManager(plugins_dir=plugins_dir)
    await pm.load_plugins()
    assert len(pm.rings[PluginHook.PRE_FLIGHT]) == 1

    # Patch _build_plugin_state to record what self.rings looks like at the
    # moment we *would* be in the middle of a clear-then-rebuild. With the
    # atomic fix, self.rings is the OLD state until the swap happens.
    original_build = pm._build_plugin_state
    observed_during_build = []

    async def spying_build(**kw):
        observed_during_build.append(list(pm.rings[PluginHook.PRE_FLIGHT]))
        return await original_build(**kw)

    pm._build_plugin_state = spying_build  # type: ignore[assignment]
    await pm.hot_swap()

    # During build, self.rings still pointed to the OLD non-empty list.
    assert observed_during_build, "spy was not invoked"
    assert len(observed_during_build[0]) == 1, (
        f"during-build snapshot was empty — old state was prematurely cleared: "
        f"{observed_during_build}"
    )
    # And after swap, the new state is also non-empty.
    assert len(pm.rings[PluginHook.PRE_FLIGHT]) == 1


def _manifest(tmp_path, entries):
    import yaml as _yaml

    with open(tmp_path / "manifest.yaml", "w") as f:
        _yaml.safe_dump({"plugins": entries}, f)


def _entry(name, **extra):
    return {"name": name, "hook": "pre_flight", "type": "python", "entrypoint": f"{name}:execute", **extra}


_NEEDS_ORCHESTRATOR_SRC = """
async def execute(ctx):
    ctx.require_rotator()
"""


@pytest.mark.asyncio
async def test_a_reload_does_not_run_the_rings_on_a_made_up_request(tmp_path):
    """The reload used to run every ring on an empty request with no
    orchestrator in it. The default plugins need the orchestrator, so with the
    shipped manifest every reload was rolled back: toggle, install and uninstall
    could not take effect."""
    from core.plugin_engine import PluginHook, PluginManager

    _write_plugin_file(str(tmp_path), "needs_it", _NEEDS_ORCHESTRATOR_SRC)
    _manifest(tmp_path, [_entry("needs_it")])
    pm = PluginManager(plugins_dir=str(tmp_path))
    await pm.load_plugins()
    ran = []

    async def spy(hook, context):
        ran.append(hook)

    pm.execute_ring = spy  # type: ignore[assignment]
    before = pm.rings

    await pm.hot_swap()

    assert ran == []
    assert pm.rings is not before  # the new set is live
    assert [p["name"] for p in pm.rings[PluginHook.PRE_FLIGHT]] == ["needs_it"]


@pytest.mark.asyncio
async def test_a_reload_with_a_plugin_that_does_not_load_changes_nothing(tmp_path):
    from core.plugin_engine import PluginHook, PluginLoadError, PluginManager

    _write_plugin_file(str(tmp_path), "p1", _VALID_PLUGIN_SRC)
    _manifest(tmp_path, [_entry("p1")])
    pm = PluginManager(plugins_dir=str(tmp_path))
    await pm.load_plugins()
    snapshot = (pm.rings, pm._plugin_meta, pm._plugin_instances, pm._plugin_stats)

    _manifest(tmp_path, [_entry("p1"), _entry("missing_file")])
    with pytest.raises(PluginLoadError, match="missing_file"):
        await pm.hot_swap()

    assert (pm.rings, pm._plugin_meta, pm._plugin_instances, pm._plugin_stats) == snapshot
    assert pm.rings is snapshot[0]
    assert [p["name"] for p in pm.rings[PluginHook.PRE_FLIGHT]] == ["p1"]


@pytest.mark.asyncio
async def test_a_refused_install_is_taken_back_out_of_the_manifest(tmp_path):
    """The entry was written before the reload and left there when the reload
    refused it, to be tried again by every later load."""
    import yaml as _yaml

    from core.plugin_engine import PluginHook, PluginManager

    _write_plugin_file(str(tmp_path), "p1", _VALID_PLUGIN_SRC)
    _manifest(tmp_path, [_entry("p1")])
    pm = PluginManager(plugins_dir=str(tmp_path))
    await pm.load_plugins()

    with pytest.raises(Exception):  # noqa: B017 - refused, whatever the reason
        await pm.install_plugin(_entry("not_there"))

    with open(pm.installed_dir + "/manifest.yaml") as f:
        assert (_yaml.safe_load(f) or {}).get("plugins") == []
    await pm.hot_swap()  # a later reload is not poisoned
    assert [p["name"] for p in pm.rings[PluginHook.PRE_FLIGHT]] == ["p1"]


@pytest.mark.asyncio
async def test_a_plugin_the_loader_refuses_does_not_stop_the_others_at_startup(tmp_path):
    """PluginSecurityError was not among the exceptions caught per plugin, so
    one refused entry took the whole load, and the start, down with it."""
    from core.plugin_engine import PluginHook, PluginManager

    _write_plugin_file(str(tmp_path), "p1", _VALID_PLUGIN_SRC)
    _write_plugin_file(str(tmp_path), "tampered", _VALID_PLUGIN_SRC)
    _manifest(tmp_path, [_entry("tampered", sha256="0" * 64), _entry("p1")])
    pm = PluginManager(plugins_dir=str(tmp_path))

    await pm.load_plugins()

    assert [p["name"] for p in pm.rings[PluginHook.PRE_FLIGHT]] == ["p1"]


# ── S1: plugin trust gate (in-process python requires trust or opt-in) ──
import pytest as _pytest  # noqa: E402

from core.plugin_engine import PluginManager  # noqa: E402


def _pinfo(source, **extra):
    return {
        "name": "evil_probe",
        "hook": "pre_flight",
        "entrypoint": "installed.nonexistent_evil_probe:Evil",
        "type": "python",
        "_source": source,
        **extra,
    }


@_pytest.mark.asyncio
async def test_installed_python_plugin_refused_without_optin():
    pm = PluginManager(plugins_dir="plugins")
    with _pytest.raises(PluginSecurityError, match="installed manifest"):
        await pm._load_plugin(_pinfo("installed"))


@_pytest.mark.asyncio
async def test_installed_python_plugin_passes_gate_with_optin():
    pm = PluginManager(plugins_dir="plugins")
    # Gate passes → the load then fails on the missing file, NOT on the gate.
    with _pytest.raises((FileNotFoundError, ImportError)):
        await pm._load_plugin(_pinfo("installed", allow_inprocess=True))


@_pytest.mark.asyncio
async def test_bundled_python_plugin_passes_gate():
    pm = PluginManager(plugins_dir="plugins")
    with _pytest.raises((FileNotFoundError, ImportError)):
        await pm._load_plugin(_pinfo("bundled"))
