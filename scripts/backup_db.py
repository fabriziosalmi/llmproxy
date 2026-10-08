#!/usr/bin/env python3
"""Back up the llmproxy database — the one artefact nothing else protects.

This project already takes timestamped backups of three things before it
modifies them: config.yaml on every apply, the systemd unit before a deploy
patches it, and the environment file before key rotation. All three can be
reconstructed by hand. The database cannot: data/endpoints.db holds the
endpoint registry, app_state (including the persisted daily budget), the
spend ledger, the RBAC subjects, and the tamper-evident audit chain whose
whole purpose is to be a record nobody can quietly alter. It had no backup
mechanism at all.

Uses SQLite's own backup API rather than copying the file. A plain `cp` of a
live database can capture a torn page or miss a WAL segment, producing a file
that opens and is subtly wrong — the worst outcome for an audit chain, since
it would verify as broken rather than as absent. The backup API is safe
against a concurrent writer.

Usage:
    python scripts/backup_db.py                      # data/endpoints.db -> data/backups/
    python scripts/backup_db.py --db path/to.db --out /backups
    python scripts/backup_db.py --keep 14            # prune older than the last 14
    python scripts/backup_db.py --verify-only FILE   # check a backup is readable
    python scripts/backup_db.py --restore FILE       # put a backup in place (proxy stopped)

Exit codes: 0 on success, 1 on failure. Prints the backup path on success so a
caller can act on it.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

DEFAULT_DB = "data/endpoints.db"
DEFAULT_OUT = "data/backups"

# Tables whose row counts are reported, so the operator sees at a glance that
# the backup holds what they expect rather than an empty schema.
REPORTED_TABLES = ("endpoints", "app_state", "spend_log", "audit_log", "user_roles")


def _counts(path: Path) -> dict[str, int | str]:
    out: dict[str, int | str] = {}
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        for table in REPORTED_TABLES:
            try:
                out[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except sqlite3.Error as exc:
                out[table] = f"unavailable ({exc})"
    finally:
        conn.close()
    return out


def verify(path: Path) -> bool:
    """Open the backup read-only and run SQLite's own integrity check."""
    if not path.exists():
        print(f"ERROR: {path} does not exist", file=sys.stderr)
        return False
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error as exc:
        print(f"ERROR: {path} is not a readable database: {exc}", file=sys.stderr)
        return False
    if result != "ok":
        print(f"ERROR: integrity check on {path} returned {result!r}", file=sys.stderr)
        return False
    print(f"verified: {path} (integrity_check ok)")
    for table, count in _counts(path).items():
        print(f"  {table}: {count}")
    return True


def backup(db: Path, out_dir: Path) -> Path:
    if not db.exists():
        raise FileNotFoundError(f"{db} does not exist")
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{db.name}.bak.{int(time.time())}"

    source = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        dest = sqlite3.connect(target)
        try:
            source.backup(dest)  # safe against a concurrent writer
            dest.commit()
        finally:
            dest.close()
    finally:
        source.close()

    # The backup is the point of this script, so put it on disk properly
    # rather than leaving it in the page cache.
    fd = os.open(target, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    dir_fd = os.open(out_dir, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)

    os.chmod(target, 0o600)
    return target


def prune(out_dir: Path, db_name: str, keep: int) -> list[Path]:
    """Remove all but the newest `keep` backups. Never removes the newest."""
    if keep < 1:
        raise ValueError("--keep must be at least 1")
    backups = sorted(
        out_dir.glob(f"{db_name}.bak.*"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    removed = []
    for old in backups[keep:]:
        old.unlink()
        removed.append(old)
    return removed


def _sidecars(db: Path) -> list[Path]:
    """The WAL and shared-memory files SQLite keeps beside a database."""
    return [db.with_name(db.name + suffix) for suffix in ("-wal", "-shm")]


def restore(source: Path, db: Path, out_dir: Path) -> Path | None:
    """Replace `db` with the backup `source`. Returns where the old files went.

    Copying a backup over endpoints.db is not a restore. The database runs in
    WAL mode, so the live file is only half of its state: a stale -wal left
    beside the restored file is replayed on the next open, and the rows written
    after the backup reappear on top of it. A backup of 5 rows restored over a
    database whose WAL held 500 later ones opened as 505. So the old database and
    its -wal/-shm are moved aside together, as a set that still opens, and only
    then is the verified backup put in place.

    The proxy must be stopped: a running process holds the old files open.
    """
    if not verify(source):
        raise ValueError(f"{source} is not a usable backup; nothing was changed")
    db.parent.mkdir(parents=True, exist_ok=True)

    # Stage next to the target first, so a failed copy leaves the live database
    # exactly as it was.
    staged = db.with_name(db.name + ".restoring")
    shutil.copyfile(source, staged)
    fd = os.open(staged, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    os.chmod(staged, 0o600)

    stash: Path | None = None
    existing = [p for p in (db, *_sidecars(db)) if p.exists()]
    if existing:
        stash = out_dir / f"pre-restore.{int(time.time())}"
        stash.mkdir(parents=True, exist_ok=True)
        for path in existing:
            shutil.move(str(path), stash / path.name)

    os.replace(staged, db)
    dir_fd = os.open(db.parent, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
    return stash


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--db", default=DEFAULT_DB, help=f"default: {DEFAULT_DB}")
    parser.add_argument("--out", default=DEFAULT_OUT, help=f"default: {DEFAULT_OUT}")
    parser.add_argument(
        "--keep",
        type=int,
        default=None,
        help="how many backups to retain after taking one (default: 7)",
    )
    parser.add_argument("--verify-only", metavar="FILE", help="verify a backup and exit")
    parser.add_argument(
        "--restore",
        metavar="FILE",
        help="replace the database with this backup; stop the proxy first",
    )
    args = parser.parse_args()

    # --verify-only used to swallow --keep: a crontab line carrying both ran,
    # checked the newest old backup, exited 0 and never took one.
    if args.verify_only and (args.keep is not None or args.restore):
        parser.error("--verify-only only checks one file; it takes no other action")
    if args.restore and args.keep is not None:
        parser.error("--restore does not take --keep")

    if args.verify_only:
        return 0 if verify(Path(args.verify_only)) else 1

    if args.restore:
        try:
            stash = restore(Path(args.restore), Path(args.db), Path(args.out))
        except (OSError, ValueError, sqlite3.Error) as exc:
            print(f"ERROR: restore failed: {exc}", file=sys.stderr)
            return 1
        if stash:
            print(f"previous database set aside in {stash}")
        return 0 if verify(Path(args.db)) else 1

    keep = 7 if args.keep is None else args.keep

    db = Path(args.db)
    out_dir = Path(args.out)
    try:
        target = backup(db, out_dir)
    except (OSError, sqlite3.Error) as exc:
        print(f"ERROR: backup failed: {exc}", file=sys.stderr)
        return 1

    if not verify(target):
        return 1

    for removed in prune(out_dir, db.name, keep):
        print(f"pruned: {removed}")

    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
