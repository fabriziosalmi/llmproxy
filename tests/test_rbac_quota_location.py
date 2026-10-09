"""Quotas live in the data volume, and the ones in the old place are adopted once.

The quota table was created in ``endpoints.db`` relative to the working directory:
not the store's file (``data/endpoints.db``), not in the volume, so a container
restart lost every per-key quota and consumed-budget figure, and scripts/backup_db.py
never saw them. The default is data/rbac.db now; a deployment that already had
quotas keeps them.
"""

import os
import sqlite3

from core.rbac import RBACManager


def _legacy(path, rows):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE quotas (api_key TEXT PRIMARY KEY, team_name TEXT, "
        "monthly_budget REAL, consumed_budget REAL DEFAULT 0.0, hard_limit BOOLEAN DEFAULT 1)"
    )
    conn.executemany("INSERT INTO quotas VALUES (?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


def _quotas(path):
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT api_key, team_name, monthly_budget, consumed_budget, hard_limit FROM quotas ORDER BY api_key").fetchall()
    finally:
        conn.close()


def test_the_default_location_is_under_data():
    assert RBACManager.DEFAULT_DB_PATH == "data/rbac.db"


def test_the_parent_directory_is_created(tmp_path):
    RBACManager(str(tmp_path / "data" / "rbac.db"))

    assert (tmp_path / "data" / "rbac.db").is_file()


def test_quotas_in_the_old_file_are_adopted_with_their_consumed_budget(tmp_path):
    old = tmp_path / "endpoints.db"
    _legacy(old, [("k1", "team-a", 100.0, 42.5, 1), ("k2", "team-b", 10.0, 0.0, 0)])

    RBACManager(str(tmp_path / "data" / "rbac.db"), legacy_db_path=str(old))

    assert _quotas(tmp_path / "data" / "rbac.db") == [
        ("k1", "team-a", 100.0, 42.5, 1),
        ("k2", "team-b", 10.0, 0.0, 0),
    ]
    assert _quotas(old)  # the old file is left as it was


def test_adoption_happens_once_and_never_overwrites_live_quotas(tmp_path):
    old = tmp_path / "endpoints.db"
    _legacy(old, [("k1", "team-a", 100.0, 42.5, 1)])
    new = str(tmp_path / "rbac.db")
    RBACManager(new, legacy_db_path=str(old))
    live = sqlite3.connect(new)
    live.execute("UPDATE quotas SET consumed_budget = 99.0 WHERE api_key = 'k1'")
    live.commit()
    live.close()

    RBACManager(new, legacy_db_path=str(old))  # a restart

    assert _quotas(new) == [("k1", "team-a", 100.0, 99.0, 1)]


def test_a_missing_or_quotaless_old_file_is_not_an_error(tmp_path):
    RBACManager(str(tmp_path / "a.db"), legacy_db_path=str(tmp_path / "nope.db"))
    bare = tmp_path / "bare.db"
    sqlite3.connect(bare).close()

    RBACManager(str(tmp_path / "b.db"), legacy_db_path=str(bare))

    assert _quotas(tmp_path / "b.db") == []


def test_pointing_at_the_same_file_does_nothing(tmp_path):
    same = tmp_path / "endpoints.db"
    _legacy(same, [("k1", "t", 1.0, 0.0, 1)])

    RBACManager(str(same), legacy_db_path=str(same))

    assert len(_quotas(same)) == 1


def test_no_stray_file_is_created_by_adoption(tmp_path):
    old = tmp_path / "endpoints.db"
    _legacy(old, [("k1", "t", 1.0, 0.0, 1)])

    RBACManager(str(tmp_path / "rbac.db"), legacy_db_path=str(old))

    assert sorted(os.listdir(tmp_path)) == ["endpoints.db", "rbac.db"]


def test_the_orchestrator_passes_its_legacy_location():
    import ast
    import pathlib

    tree = ast.parse((pathlib.Path(__file__).resolve().parent.parent / "proxy" / "rotator.py").read_text())
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "RBACManager"]
    assert calls and all(any(k.arg == "legacy_db_path" for k in c.keywords) for c in calls)
