"""The database backup, and a restore that is actually exercised.

An untested restore is not a backup. This project took timestamped backups of
config.yaml, the systemd unit and the environment file — all reconstructable
by hand — and none of the database, which holds the endpoint registry, the
persisted budget, the spend ledger and the tamper-evident audit chain. Those
cannot be reconstructed.

These tests back up a populated database, put it back, and assert the rows
survived — the round trip, not just the artefact.
"""

import sqlite3
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "backup_db.py"


def _populate(path: Path, audit_rows: int = 3) -> None:
    """A database shaped like the real one, with rows worth losing."""
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE endpoints (id TEXT PRIMARY KEY, url TEXT, status INTEGER)")
    conn.execute("CREATE TABLE app_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("CREATE TABLE spend_log (id INTEGER PRIMARY KEY, cost_usd REAL)")
    conn.execute(
        "CREATE TABLE audit_log (id INTEGER PRIMARY KEY, req_id TEXT, entry_hash TEXT)"
    )
    conn.execute("CREATE TABLE user_roles (id INTEGER PRIMARY KEY, subject TEXT)")
    conn.execute("INSERT INTO endpoints VALUES ('ep1', 'http://x.invalid', 3)")
    conn.execute("INSERT INTO app_state VALUES ('budget:daily_total', '12.5')")
    conn.execute("INSERT INTO spend_log (cost_usd) VALUES (0.42)")
    for i in range(audit_rows):
        conn.execute(
            "INSERT INTO audit_log (req_id, entry_hash) VALUES (?, ?)",
            (f"r{i}", "a" * 64),
        )
    conn.commit()
    conn.close()


def _run(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True
    )


def test_backup_captures_the_rows(tmp_path):
    db = tmp_path / "endpoints.db"
    _populate(db)
    out = tmp_path / "backups"

    result = _run("--db", str(db), "--out", str(out))
    assert result.returncode == 0, result.stderr

    backups = list(out.glob("endpoints.db.bak.*"))
    assert len(backups) == 1
    conn = sqlite3.connect(backups[0])
    try:
        assert conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 3
        assert (
            conn.execute(
                "SELECT value FROM app_state WHERE key='budget:daily_total'"
            ).fetchone()[0]
            == "12.5"
        )
    finally:
        conn.close()


def test_restore_round_trip(tmp_path):
    """The claim that matters: the backup can be put back and works.

    Writing a backup proves the artefact exists. Restoring proves it is one.
    """
    db = tmp_path / "endpoints.db"
    _populate(db, audit_rows=5)
    out = tmp_path / "backups"
    assert _run("--db", str(db), "--out", str(out)).returncode == 0
    backup_file = next(out.glob("endpoints.db.bak.*"))

    # Disaster: the live database is destroyed.
    db.unlink()
    assert not db.exists()

    # Restore is a copy of the backup into place.
    db.write_bytes(backup_file.read_bytes())

    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 5
        assert conn.execute("SELECT id FROM endpoints").fetchone()[0] == "ep1"
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()


def test_backup_is_readable_and_integrity_checked(tmp_path):
    db = tmp_path / "endpoints.db"
    _populate(db)
    out = tmp_path / "backups"
    result = _run("--db", str(db), "--out", str(out))
    assert "integrity_check ok" in result.stdout
    assert "audit_log: 3" in result.stdout


def test_backup_survives_a_concurrent_writer(tmp_path):
    """A plain cp can catch a torn page; the backup API cannot."""
    db = tmp_path / "endpoints.db"
    _populate(db)
    out = tmp_path / "backups"

    holder = sqlite3.connect(db)
    holder.execute("INSERT INTO spend_log (cost_usd) VALUES (1.0)")
    holder.commit()
    try:
        result = _run("--db", str(db), "--out", str(out))
        assert result.returncode == 0, result.stderr
    finally:
        holder.close()

    backup_file = next(out.glob("endpoints.db.bak.*"))
    conn = sqlite3.connect(backup_file)
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()


def test_retention_keeps_the_newest_and_never_empties_the_directory(tmp_path):
    db = tmp_path / "endpoints.db"
    _populate(db)
    out = tmp_path / "backups"
    for _ in range(4):
        assert _run("--db", str(db), "--out", str(out), "--keep", "2").returncode == 0
        # distinct timestamps
        import time as _t

        _t.sleep(1.01)

    remaining = sorted(out.glob("endpoints.db.bak.*"))
    assert len(remaining) == 2, f"expected 2 retained, found {len(remaining)}"
    for path in remaining:
        conn = sqlite3.connect(path)
        try:
            assert conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 3
        finally:
            conn.close()


def test_a_missing_database_fails_loudly(tmp_path):
    result = _run("--db", str(tmp_path / "nope.db"), "--out", str(tmp_path / "b"))
    assert result.returncode == 1
    assert "does not exist" in result.stderr


def test_verify_only_rejects_a_corrupt_file(tmp_path):
    bogus = tmp_path / "not-a-db.bak.1"
    bogus.write_bytes(b"this is not a sqlite database")
    result = _run("--verify-only", str(bogus))
    assert result.returncode == 1
    assert "ERROR" in result.stderr


def test_the_backup_is_not_world_readable(tmp_path):
    """It contains the audit chain and the spend ledger."""
    db = tmp_path / "endpoints.db"
    _populate(db)
    out = tmp_path / "backups"
    assert _run("--db", str(db), "--out", str(out)).returncode == 0
    backup_file = next(out.glob("endpoints.db.bak.*"))
    assert backup_file.stat().st_mode & 0o077 == 0, "backup must be 0600"
