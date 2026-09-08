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

Exit codes: 0 on success, 1 on failure. Prints the backup path on success so a
caller can act on it.
"""

from __future__ import annotations

import argparse
import os
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--db", default=DEFAULT_DB, help=f"default: {DEFAULT_DB}")
    parser.add_argument("--out", default=DEFAULT_OUT, help=f"default: {DEFAULT_OUT}")
    parser.add_argument(
        "--keep", type=int, default=7, help="how many backups to retain (default: 7)"
    )
    parser.add_argument("--verify-only", metavar="FILE", help="verify a backup and exit")
    args = parser.parse_args()

    if args.verify_only:
        return 0 if verify(Path(args.verify_only)) else 1

    db = Path(args.db)
    out_dir = Path(args.out)
    try:
        target = backup(db, out_dir)
    except (OSError, sqlite3.Error) as exc:
        print(f"ERROR: backup failed: {exc}", file=sys.stderr)
        return 1

    if not verify(target):
        return 1

    for removed in prune(out_dir, db.name, args.keep):
        print(f"pruned: {removed}")

    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
