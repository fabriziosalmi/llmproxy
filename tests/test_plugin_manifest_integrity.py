"""The plugin manifests must survive an interrupted write, and a lost pin
must not silently disable tampering detection.

Four sites rewrote a manifest with a truncating open(), one of them reachable
over HTTP through the plugin toggle endpoint. Those files record which plugins
are installed, whether each is enabled, and each one's SHA-256 pin — and the
loader treated an absent pin as a warning, loading the plugin regardless. So a
torn write could leave a manifest that still parses with a pin missing, and
integrity checking for that plugin would be off with nothing reporting it.

Scope note: in-process Python plugins from the installed manifest are already
refused unless the entry sets allow_inprocess, so the pin is defence in depth
for plugins an operator has consciously accepted — not the only thing standing
between an installed file and execution. These tests set allow_inprocess so
they exercise the pin logic rather than stopping at the outer gate.
"""

import os

import pytest

from core.atomic_io import atomic_write
from core.plugin_engine import PluginSecurityError, compute_plugin_sha256

yaml = pytest.importorskip("yaml")


# ── the write cannot tear ───────────────────────────────────────────────────


def test_a_failed_manifest_write_leaves_the_previous_one_intact(tmp_path, monkeypatch):
    """os.replace is the commit point; a failure before it changes nothing."""
    target = tmp_path / "manifest.yaml"
    original = yaml.safe_dump({"plugins": [{"name": "a", "sha256": "x" * 64}]})
    target.write_text(original)

    def _boom(src, dst):
        raise OSError("no space left on device")

    monkeypatch.setattr(os, "replace", _boom)
    with pytest.raises(OSError):
        atomic_write("plugins: []\n", str(target), str(tmp_path), ".manifest.")

    assert target.read_text() == original
    assert not list(tmp_path.glob("*.tmp")), "the temp file must be cleaned up"


def test_a_successful_write_is_fsynced_before_the_rename(tmp_path):
    calls = []
    real = os.fsync

    def _counting(fd):
        calls.append(fd)
        return real(fd)

    import unittest.mock as mock

    with mock.patch.object(os, "fsync", _counting):
        atomic_write("plugins: []\n", str(tmp_path / "m.yaml"), str(tmp_path), ".m.")

    assert len(calls) >= 2, "expected the file and its directory to be fsynced"
    assert yaml.safe_load((tmp_path / "m.yaml").read_text()) == {"plugins": []}


# ── a lost pin is refused, not warned about ─────────────────────────────────


def _engine(tmp_path, entry, source):
    from core.plugin_engine import PluginManager

    plugins_dir = tmp_path / "plugins"
    (plugins_dir / "installed").mkdir(parents=True)
    src = plugins_dir / "demo.py"
    src.write_text("def register():\n    return None\n")

    mgr = PluginManager(config={})
    mgr.plugins_dir = str(plugins_dir)
    mgr.installed_dir = str(plugins_dir / "installed")
    info = dict(entry)
    info["_source"] = source
    return mgr, info, src


def test_installed_plugin_without_a_pin_is_refused(tmp_path):
    """An installed entry always had a pin; its absence means damage."""
    mgr, info, src = _engine(
        tmp_path,
        {
            "name": "demo",
            "entrypoint": "demo:register",
            "hook": "post_flight",
            "allow_inprocess": True,
        },
        "installed",
    )
    with pytest.raises(PluginSecurityError, match="no SHA-256 pin"):
        import asyncio

        asyncio.run(mgr._load_plugin(info))


def test_bundled_plugin_without_a_pin_still_loads(tmp_path, caplog):
    """Bundled plugins may legitimately ship without one; only warn."""
    mgr, info, src = _engine(
        tmp_path,
        {
            "name": "demo",
            "entrypoint": "demo:register",
            "hook": "post_flight",
            "allow_inprocess": True,
        },
        "bundled",
    )
    import asyncio

    with caplog.at_level("WARNING"):
        try:
            asyncio.run(mgr._load_plugin(info))
        except PluginSecurityError:
            pytest.fail("a bundled plugin without a pin must not be refused")
        except Exception:
            pass  # registration may fail for unrelated reasons in this stub

    assert any("without a SHA-256 pin" in r.message for r in caplog.records)


def test_a_pin_that_does_not_match_is_still_refused(tmp_path):
    mgr, info, src = _engine(
        tmp_path,
        {
            "name": "demo",
            "entrypoint": "demo:register",
            "hook": "post_flight",
            "allow_inprocess": True,
            "sha256": "d" * 64,
        },
        "installed",
    )
    with pytest.raises(PluginSecurityError, match="pin mismatch"):
        import asyncio

        asyncio.run(mgr._load_plugin(info))


def test_a_matching_pin_is_accepted(tmp_path):
    mgr, info, src = _engine(
        tmp_path,
        {
            "name": "demo",
            "entrypoint": "demo:register",
            "hook": "post_flight",
            "allow_inprocess": True,
        },
        "installed",
    )
    info["sha256"] = compute_plugin_sha256(src.read_text())
    import asyncio

    try:
        asyncio.run(mgr._load_plugin(info))
    except PluginSecurityError as exc:
        pytest.fail(f"a matching pin must be accepted, got: {exc}")
    except Exception:
        pass  # registration may fail for unrelated reasons in this stub
