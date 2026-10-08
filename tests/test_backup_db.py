"""The database backup, and a restore that is actually exercised.

An untested restore is not a backup. This project took timestamped backups of
config.yaml, the systemd unit and the environment file — all reconstructable
by hand — and none of the database, which holds the endpoint registry, the
persisted budget, the spend ledger and the tamper-evident audit chain. Those
cannot be reconstructed.

These tests back up a populated database, put it back, and assert the rows
survived — the round trip, not just the artefact.
"""

import re
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


# ── restore ───────────────────────────────────────────────────────────────────


def _count(path: Path, table: str = "audit_log") -> int:
    conn = sqlite3.connect(path)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def test_restore_over_a_live_wal_does_not_resurrect_later_rows(tmp_path):
    """The failure a plain `cp` has: the stale -wal is replayed onto the backup.

    The live database keeps 500 rows in its WAL (never checkpointed, as after a
    crash). Copying the 5-row backup over endpoints.db leaves that WAL beside it
    and the database opens with 505 rows. --restore must end with exactly 5.
    """
    db = tmp_path / "endpoints.db"
    _populate(db, audit_rows=5)
    out = tmp_path / "backups"
    assert _run("--db", str(db), "--out", str(out)).returncode == 0
    backup_file = next(out.glob("endpoints.db.bak.*"))

    live = sqlite3.connect(db)
    live.execute("PRAGMA journal_mode=WAL")
    live.execute("PRAGMA wal_autocheckpoint=0")
    for i in range(500):
        live.execute(
            "INSERT INTO audit_log (req_id, entry_hash) VALUES (?, ?)", (f"late{i}", "b" * 64)
        )
    live.commit()
    wal = db.with_name(db.name + "-wal")
    assert wal.exists() and wal.stat().st_size > 0  # the situation under test

    try:
        result = _run("--restore", str(backup_file), "--db", str(db), "--out", str(out))
    finally:
        live.close()
    assert result.returncode == 0, result.stderr

    assert _count(db) == 5
    stashes = list(out.glob("pre-restore.*"))
    assert len(stashes) == 1
    assert _count(stashes[0] / "endpoints.db") == 505  # the old set still opens


def test_restore_refuses_a_corrupt_backup_and_touches_nothing(tmp_path):
    db = tmp_path / "endpoints.db"
    _populate(db, audit_rows=4)
    bad = tmp_path / "bad.bak"
    bad.write_bytes(b"this is not a database" * 100)

    result = _run("--restore", str(bad), "--db", str(db), "--out", str(tmp_path / "b"))

    assert result.returncode == 1
    assert _count(db) == 4
    assert not list(tmp_path.glob("**/pre-restore.*"))
    assert not db.with_name(db.name + ".restoring").exists()


def test_restore_into_an_empty_place_just_installs_the_backup(tmp_path):
    src = tmp_path / "src.db"
    _populate(src, audit_rows=2)
    out = tmp_path / "backups"
    assert _run("--db", str(src), "--out", str(out)).returncode == 0
    backup_file = next(out.glob("src.db.bak.*"))

    target = tmp_path / "data" / "endpoints.db"
    result = _run("--restore", str(backup_file), "--db", str(target), "--out", str(out))

    assert result.returncode == 0, result.stderr
    assert _count(target) == 2
    assert (target.stat().st_mode & 0o777) == 0o600


def test_verify_only_cannot_be_combined_with_keep(tmp_path):
    """It used to accept --keep and ignore it, which is how a crontab line that
    never took a backup ran green for as long as an old backup existed."""
    db = tmp_path / "endpoints.db"
    _populate(db)
    out = tmp_path / "backups"
    assert _run("--db", str(db), "--out", str(out)).returncode == 0
    backup_file = next(out.glob("endpoints.db.bak.*"))

    result = _run("--keep", "14", "--verify-only", str(backup_file))

    assert result.returncode == 2
    assert "--verify-only" in result.stderr


# ── the documented schedule ───────────────────────────────────────────────────


def test_the_documented_cron_line_takes_a_backup(tmp_path):
    """Run the crontab line from docs/guide/deployment.md, not a paraphrase of it.

    The documented line used to pass --verify-only: it checked the newest
    existing backup, exited 0 and took nothing, so once any backup existed the
    schedule reported green indefinitely.
    """
    doc = (REPO / "docs" / "guide" / "deployment.md").read_text()
    block = re.search(r"```cron\n(.*?)```", doc, re.S)
    assert block, "deployment.md no longer documents a cron schedule"
    lines = [ln for ln in block.group(1).splitlines() if ln and not ln.startswith("#")]
    assert len(lines) == 1
    command = lines[0].split(None, 5)[5]  # drop the five time fields

    data = tmp_path / "data"
    data.mkdir()
    _populate(data / "endpoints.db")
    assert "backup_db.py" in command and "cd /opt/llmproxy" in command
    command = command.replace("cd /opt/llmproxy", f"cd {tmp_path}").replace(
        "python scripts/backup_db.py", f"{sys.executable} {SCRIPT}"
    )

    result = subprocess.run(["sh", "-c", command], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "" and result.stderr == ""  # quiet unless it fails
    assert len(list((data / "backups").glob("endpoints.db.bak.*"))) == 1
