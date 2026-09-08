"""Config editing from the Admin UI: /api/v1/config/{raw,validate,apply}.

The apply path must NEVER write an invalid config, and validate is a pure
dry-run. Admin auth is exercised via auth_enabled=False (open in dev mode);
the auth gate itself is covered by the existing admin-auth tests."""
import pytest
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, MagicMock

from tests.conftest import InMemoryRepository, minimal_config


def _app(config_path: str = "config.yaml"):
    agent = MagicMock()
    agent.config = minimal_config(auth_enabled=False)
    agent.store = InMemoryRepository()
    agent.config_path = config_path
    app = FastAPI()
    from proxy.routes.config import create_router as config

    app.include_router(config(agent))
    return app, agent


async def _post(app, path, payload):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return await c.post(path, json=payload)


async def _get(app, path):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return await c.get(path)


@pytest.mark.asyncio
async def test_validate_rejects_broken_yaml():
    app, _ = _app()
    r = await _post(app, "/api/v1/config/validate", {"yaml": "key: [unclosed"})
    assert r.status_code == 200
    data = r.json()
    assert data["valid"] is False
    assert data["errors"]


@pytest.mark.asyncio
async def test_validate_rejects_non_mapping_root():
    app, _ = _app()
    r = await _post(app, "/api/v1/config/validate", {"yaml": "- a\n- b\n"})
    assert r.json()["valid"] is False


@pytest.mark.asyncio
async def test_validate_accepts_good_config():
    app, _ = _app()
    good = "server:\n  auth:\n    enabled: false\nendpoints: {}\n"
    r = await _post(app, "/api/v1/config/validate", {"yaml": good})
    assert r.status_code == 200
    assert r.json()["valid"] is True


@pytest.mark.asyncio
async def test_validate_too_large_is_rejected():
    app, _ = _app()
    huge = "x: " + "a" * (256 * 1024 + 10)
    r = await _post(app, "/api/v1/config/validate", {"yaml": huge})
    assert r.status_code == 413


@pytest.mark.asyncio
async def test_apply_invalid_does_not_write(tmp_path):
    cfg = tmp_path / "config.yaml"
    original = "server:\n  port: 8090\n"
    cfg.write_text(original)
    app, _ = _app(str(cfg))
    # Broken YAML fails validation → 400 before any write, file untouched.
    r = await _post(app, "/api/v1/config/apply", {"yaml": "key: [unclosed"})
    assert r.status_code == 400
    assert cfg.read_text() == original

    # The error envelope must be shaped like every other one in the package:
    # `detail` is a human-readable string, never an object. This route used to
    # be the single exception among 87 raise sites, so no client could parse
    # errors uniformly. The structured reasons live alongside it, not inside it.
    body = r.json()
    assert isinstance(body.get("detail"), str), (
        f"`detail` must be a string, got {type(body.get('detail')).__name__}"
    )
    assert isinstance(body.get("errors"), list) and body["errors"], (
        "validation reasons must still be returned, at the top level"
    )
    assert isinstance(body.get("warnings"), list)


@pytest.mark.asyncio
async def test_raw_returns_on_disk_source(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("server:\n  port: 8090\n")
    app, _ = _app(str(cfg))
    r = await _get(app, "/api/v1/config/raw")
    assert r.status_code == 200
    assert "port: 8090" in r.json()["yaml"]


# ── the backup has to be restorable, which means it has to be whole ─────────


def _reloadable(agent):
    """Make _reload_from_disk survive a MagicMock agent.

    The apply path reloads after writing; these tests are about the writing,
    so the reload is given real values rather than MagicMock attributes.
    """
    agent._load_config = lambda: minimal_config(auth_enabled=False)
    agent._compute_config_hash_sync = lambda: "hash"
    agent.webhooks = None
    agent._add_log = AsyncMock()
    return agent


@pytest.mark.asyncio
async def test_backup_is_a_byte_exact_copy_of_the_replaced_config(tmp_path):
    """The .bak is the whole previous file, not a prefix of it."""
    cfg = tmp_path / "config.yaml"
    original = "server:\n  auth:\n    enabled: false\nendpoints: {}\n# trailing marker\n"
    cfg.write_text(original)
    app, agent = _app(str(cfg))
    _reloadable(agent)

    new = "server:\n  auth:\n    enabled: false\nendpoints: {}\n# replaced\n"
    r = await _post(app, "/api/v1/config/apply", {"yaml": new})
    assert r.status_code == 200, r.text

    backups = list(tmp_path.glob("config.yaml.bak.*"))
    assert len(backups) == 1, f"expected exactly one backup, got {backups}"
    assert backups[0].read_text() == original, (
        "the backup must be the complete previous config — a truncated YAML "
        "document often still parses, so a torn backup restores silently wrong"
    )
    assert cfg.read_text() == new


@pytest.mark.asyncio
async def test_apply_leaves_no_temporary_files_behind(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("server:\n  auth:\n    enabled: false\nendpoints: {}\n")
    app, agent = _app(str(cfg))
    _reloadable(agent)

    r = await _post(
        app,
        "/api/v1/config/apply",
        {"yaml": "server:\n  auth:\n    enabled: false\nendpoints: {}\n# v2\n"},
    )
    assert r.status_code == 200, r.text
    assert not list(tmp_path.glob("*.tmp")), "atomic write leaked a temp file"


def test_interrupted_write_leaves_the_target_untouched(tmp_path, monkeypatch):
    """The property the old truncating write could not have.

    `open(path, "w")` destroys the file before it writes a byte, so a failure
    between the two leaves a half-written config on disk. Writing to a temp
    file and renaming means the failure can only ever destroy the temp file.
    """
    import os as _os

    from proxy.routes.config import _atomic_write

    target = tmp_path / "config.yaml"
    original = "server:\n  port: 8090\n"
    target.write_text(original)

    def _boom(src, dst):
        raise OSError("no space left on device")

    monkeypatch.setattr(_os, "replace", _boom)
    with pytest.raises(OSError):
        _atomic_write("new content", str(target), str(tmp_path), ".config.")

    assert target.read_text() == original, "a failed write must not touch the target"
    assert not list(tmp_path.glob("*.tmp")), "a failed write must clean up its temp file"


@pytest.mark.asyncio
async def test_apply_fsyncs_before_it_points_at_the_new_bytes(tmp_path):
    """Durability, not just atomicity.

    os.replace is atomic with respect to readers, but the bytes it renames
    into place can still be sitting in the page cache when the machine loses
    power — leaving a config.yaml (or a .bak) whose name exists and whose
    contents do not. The apply path was doing neither: no temp file and no
    fsync. Counting the fsyncs is the only way to observe from a test that
    the data was pushed down before the rename made it visible.
    """
    import os as _os

    cfg = tmp_path / "config.yaml"
    cfg.write_text("server:\n  auth:\n    enabled: false\nendpoints: {}\n")
    app, agent = _app(str(cfg))
    _reloadable(agent)

    calls = []
    real_fsync = _os.fsync

    def _counting_fsync(fd):
        calls.append(fd)
        return real_fsync(fd)

    from unittest.mock import patch

    with patch.object(_os, "fsync", _counting_fsync):
        r = await _post(
            app,
            "/api/v1/config/apply",
            {"yaml": "server:\n  auth:\n    enabled: false\nendpoints: {}\n# v2\n"},
        )
    assert r.status_code == 200, r.text
    # Two writes (backup, config), each fsyncing the file and its directory.
    assert len(calls) >= 4, (
        f"expected the backup and the config to be fsynced before their "
        f"renames, saw {len(calls)} fsync call(s)"
    )
