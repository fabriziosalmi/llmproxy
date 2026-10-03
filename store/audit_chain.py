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

#: Rows the stores read per page while verifying. The whole chain is checked, a
#: page at a time, so memory stays flat however long the log is.
VERIFY_PAGE_SIZE = 5_000


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


class ChainVerifier:
    """Checks the chain incrementally: feed pages of rows in ascending id order.

    The stores used to read ``ORDER BY id ASC LIMIT 100000`` into one list, which
    verified the *oldest* 100,000 rows while a comment claimed it checked the most
    recent: every row after that was never examined, yet the answer was still
    ``valid`` (one audit row is written per request). Carrying the state between
    pages covers the whole chain without holding it in memory.
    """

    def __init__(
        self,
        gaps: list[dict[str, Any]] | None = None,
        anchor: dict[str, Any] | None = None,
    ):
        self._by_end = {g["end"]: g for g in (gaps or [])}
        self._expected_prev = GENESIS
        self.verified = 0
        self.rows_removed = 0
        self.total = 0
        # An externally recorded head ({"id", "hash"}) to check the chain against.
        self._anchor = anchor
        self._first_id: int | None = None
        self._last_id: int | None = None
        self._anchor_hash_seen: str | None = None

    def _broken(self, row: dict[str, Any], error: str) -> dict[str, Any]:
        return {
            "valid": False,
            # Rows examined up to and including the one that failed.
            "total": self.total,
            "verified": self.verified,
            "broken_at": row.get("id"),
            "error": error,
        }

    def feed(self, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Check the next rows. Returns the failure result, or None to continue."""
        for row in rows:
            self.total += 1
            row_id = row.get("id")
            if isinstance(row_id, int):
                if self._first_id is None:
                    self._first_id = row_id
                self._last_id = row_id
                if self._anchor and row_id == self._anchor["id"]:
                    self._anchor_hash_seen = row.get("entry_hash", "")
            stored_hash = row.get("entry_hash", "")
            stored_prev = row.get("prev_hash", "")

            # Blank entry_hash: tolerate ONLY for leading legacy rows written
            # before the hash-chain migration (no hashed row seen yet). A blank
            # hash AFTER hashed rows is an attacker blanking a row to truncate
            # the tail and re-anchor the chain to GENESIS: a break, not a reset.
            if not stored_hash:
                if self.verified == 0:
                    self._expected_prev = GENESIS
                    continue
                return self._broken(
                    row,
                    f"blank entry_hash at id={row.get('id')} after hashed rows (tamper detected)",
                )

            if stored_prev != self._expected_prev:
                bridged = _bridged(stored_prev, self._expected_prev, self._by_end)
                if not bridged:
                    return self._broken(row, f"prev_hash mismatch at id={row.get('id')}")
                self.rows_removed += bridged

            if entry_hash(stored_prev, row) != stored_hash:
                return self._broken(
                    row, f"entry_hash mismatch at id={row.get('id')} (tamper detected)"
                )

            self._expected_prev = stored_hash
            self.verified += 1
        return None

    def _check_anchor(self) -> tuple[str, str | None]:
        """(status, error) for the externally recorded head, after the walk."""
        assert self._anchor is not None  # nosec B101 - callers check
        want_id, want_hash = self._anchor["id"], self._anchor["hash"]
        if self._anchor_hash_seen is not None:
            if self._anchor_hash_seen == want_hash:
                return "ok", None
            return "mismatch", (
                f"anchor mismatch at id={want_id}: the externally recorded head "
                "hash differs from the chain (rows before it were rewritten)"
            )
        # The anchored row is not in the chain.
        if self._last_id is None or want_id > self._last_id:
            return "truncated", (
                f"chain truncated: the recorded head is id={want_id} but the chain "
                f"ends at id={self._last_id if self._last_id is not None else 0}"
            )
        if self._first_id is not None and want_id < self._first_id:
            # Older than the oldest retained row: removed by the retention purge.
            return "purged", None
        if want_hash in self._by_end:
            return "erased", None  # removed by a recorded erasure
        return "missing", (
            f"the recorded head id={want_id} is missing from the chain and no "
            "recorded deletion accounts for it"
        )

    def result(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "valid": True,
            "total": self.total,
            "verified": self.verified,
            "broken_at": None,
            # Rows removed by a recorded retention purge or erasure and bridged
            # over; visible so "valid" is not mistaken for "nothing was removed".
            "rows_removed": self.rows_removed,
        }
        if self._anchor is not None:
            status, error = self._check_anchor()
            result["anchor"] = {"id": self._anchor["id"], "status": status}
            if error:
                result.update(valid=False, broken_at=self._anchor["id"], error=error)
        return result


def verify_rows(
    rows: list[dict[str, Any]],
    gaps: list[dict[str, Any]] | None = None,
    anchor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Verify a complete list of rows (ascending id) in one call."""
    verifier = ChainVerifier(gaps, anchor)
    return verifier.feed(rows) or verifier.result()


def chain_head(last_row: tuple[Any, ...] | None) -> dict[str, Any]:
    """The current head of the chain from ``(id, entry_hash, row_count)`` or None.

    The head is the one short value (id and hash of the newest row, plus the row
    count) worth recording OUTSIDE the database: a log line, a ticket, a monitor.
    The chain is keyless SHA-256, so someone who can write the database can edit a
    row and recompute everything after it, or delete the newest rows, and the
    chain still verifies against itself. Checked against a head recorded
    elsewhere, either shows up (see ChainVerifier's ``anchor``).
    """
    if last_row is None:
        return {"id": 0, "hash": GENESIS, "count": 0}
    row_id, entry_hash_, count = last_row
    return {"id": int(row_id), "hash": entry_hash_ or "", "count": int(count)}


def parse_anchor(anchor_id: Any, anchor_hash: Any) -> dict[str, Any] | None:
    """Validate an externally recorded head; None when neither part was given."""
    if anchor_id is None and anchor_hash is None:
        return None
    if anchor_id is None or anchor_hash is None:
        raise ValueError("anchor_id and anchor_hash must be given together")
    try:
        row_id = int(anchor_id)
    except (TypeError, ValueError):
        raise ValueError("anchor_id must be an integer") from None
    digest = str(anchor_hash).strip().lower()
    if row_id < 1 or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("anchor_hash must be the 64-character hex entry hash of a row")
    return {"id": row_id, "hash": digest}
