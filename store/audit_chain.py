"""The audit hash chain's verification, and how deleting from it is recorded.

Each audit row's entry_hash covers the previous row's hash, so removing a row
from the chain breaks it at the next row. That is the point of the chain, but
two legitimate operations remove rows: the retention purge (the oldest rows,
daily, by default after 90 days) and GDPR erasure (one subject's rows, from
anywhere in the chain). Neither was recorded, so the verifier saw an ordinary
purge as tampering: it expected the first remaining row to link to GENESIS,
reported ``prev_hash mismatch`` on every deployment older than the retention
window, and stayed red from then on, which teaches operators to ignore the one
check that exists to be believed.

A legitimate deletion now leaves a *gap record*: the hash that preceded the
removed run and the hash of its last row. That is enough to prove the next
surviving row followed the removed run and nothing else, and it holds hashes
only, no row content. The verifier bridges a break only when a gap record
accounts for exactly that break; a row deleted by any other route still breaks
the chain, as before.

Both stores share this module so the two backends cannot disagree about what a
valid chain is.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

#: app_state key holding the gap records, as a JSON list.
GAPS_KEY = "audit_chain_gaps"

GENESIS = "GENESIS"

#: Rows examined per verification. Unchanged from the stores' original limit.
MAX_VERIFY_ROWS = 100_000


def entry_hash(prev_hash: str, row: dict[str, Any]) -> str:
    """The hash a stored row should carry, given the hash before it."""
    payload = (
        f"{prev_hash}|{row['ts']}|{row['req_id']}|{row['session_id']}|"
        f"{row['key_prefix']}|{row['model']}|{row['provider']}|{row['status']}|"
        f"{row['prompt_tokens']}|{row['completion_tokens']}|{row['cost_usd']}|"
        f"{row['latency_ms']}|{row['blocked']}|{row['block_reason']}|{row['metadata']}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ── recording a deletion ─────────────────────────────────────────────────────


def load_gaps(raw: Any) -> list[dict[str, Any]]:
    """Gap records from app_state (already JSON-decoded, or None)."""
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    return [g for g in raw if isinstance(g, dict) and g.get("start") and g.get("end")]


def segments_from_rows(
    removed: list[dict[str, Any]], *, reason: str, now: float | None = None
) -> list[dict[str, Any]]:
    """Gap records for rows about to be deleted, in chain (id) order.

    Rows that follow one another in the chain become one segment. Legacy rows
    from before the hash chain (blank entry_hash) are not part of it and are
    skipped.
    """
    at = int(now if now is not None else time.time())
    segments: list[dict[str, Any]] = []
    for row in sorted(removed, key=lambda r: r["id"]):
        if not row.get("entry_hash"):
            continue
        if segments and segments[-1]["end"] == row["prev_hash"]:
            segments[-1]["end"] = row["entry_hash"]
            segments[-1]["rows"] += 1
        else:
            segments.append(
                {
                    "start": row["prev_hash"],
                    "end": row["entry_hash"],
                    "rows": 1,
                    "reason": reason,
                    "at": at,
                }
            )
    return segments


def merge_gaps(
    existing: list[dict[str, Any]], new: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Add segments, joining any that now run end to start.

    A second retention purge removes the rows right after the first one's, so
    its segment starts where the first ended; joining them keeps the record at
    one entry for the whole removed prefix instead of one per day.
    """
    gaps = [dict(g) for g in existing] + [dict(g) for g in new]
    merged = True
    while merged:
        merged = False
        by_start = {g["start"]: g for g in gaps}
        for g in gaps:
            nxt = by_start.get(g["end"])
            if nxt is not None and nxt is not g:
                joined = {
                    "start": g["start"],
                    "end": nxt["end"],
                    "rows": g.get("rows", 0) + nxt.get("rows", 0),
                    "reason": g["reason"] if g.get("reason") == nxt.get("reason") else "mixed",
                    "at": max(g.get("at", 0), nxt.get("at", 0)),
                }
                gaps = [x for x in gaps if x is not g and x is not nxt] + [joined]
                merged = True
                break
    return gaps


# ── verification ─────────────────────────────────────────────────────────────


def _bridged(stored_prev: str, expected_prev: str, by_end: dict[str, dict[str, Any]]) -> int:
    """Rows a gap record accounts for between ``expected_prev`` and ``stored_prev``.

    Zero means no recorded deletion explains the break.
    """
    removed = 0
    seen: set[str] = set()
    cursor = stored_prev
    while cursor in by_end and cursor not in seen:
        seen.add(cursor)
        gap = by_end[cursor]
        removed += int(gap.get("rows", 0))
        if gap["start"] == expected_prev:
            return max(removed, 1)
        cursor = gap["start"]
    return 0


def verify_rows(
    rows: list[dict[str, Any]], gaps: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Walk ``rows`` (ascending id) and check the chain.

    ``gaps`` are the recorded deletions; without them every removal is a break.
    """
    by_end = {g["end"]: g for g in (gaps or [])}
    expected_prev = GENESIS
    verified = 0
    rows_removed = 0
    total = len(rows)

    def broken(row: dict[str, Any], error: str) -> dict[str, Any]:
        return {
            "valid": False,
            "total": total,
            "verified": verified,
            "broken_at": row.get("id"),
            "error": error,
        }

    for row in rows:
        stored_hash = row.get("entry_hash", "")
        stored_prev = row.get("prev_hash", "")

        # Blank entry_hash: tolerate ONLY for leading legacy rows written before
        # the hash-chain migration (no hashed row seen yet). A blank hash AFTER
        # hashed rows is an attacker blanking a row to truncate the tail and
        # re-anchor the chain to GENESIS: a break, not a reset.
        if not stored_hash:
            if verified == 0:
                expected_prev = GENESIS
                continue
            return broken(
                row,
                f"blank entry_hash at id={row.get('id')} after hashed rows (tamper detected)",
            )

        if stored_prev != expected_prev:
            bridged = _bridged(stored_prev, expected_prev, by_end)
            if not bridged:
                return broken(row, f"prev_hash mismatch at id={row.get('id')}")
            rows_removed += bridged

        if entry_hash(stored_prev, row) != stored_hash:
            return broken(
                row, f"entry_hash mismatch at id={row.get('id')} (tamper detected)"
            )

        expected_prev = stored_hash
        verified += 1

    return {
        "valid": True,
        "total": total,
        "verified": verified,
        "broken_at": None,
        # Rows removed by a recorded retention purge or erasure and bridged
        # over; visible so "valid" is not mistaken for "nothing was removed".
        "rows_removed": rows_removed,
    }
