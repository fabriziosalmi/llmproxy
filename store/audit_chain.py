"""The audit hash chain: how a row is sealed, how the chain is verified, and how
deleting from it is recorded.

Each audit row's entry_hash covers the previous row's hash, so removing a row
from the chain breaks it at the next row. That is the point of the chain, but
two legitimate operations remove rows: the retention purge (the oldest rows,
daily, by default after 90 days) and GDPR erasure (one subject's rows, from
anywhere in the chain).

A legitimate deletion leaves a *removal record*: the hash that preceded the
removed run and the hash of its last row, hashes only, no row content. The
record is itself a row of the chain (``EVENT_ROWS_REMOVED``), appended in the
transaction that deletes. It used to be a JSON value in ``app_state``, next to
the table it vouched for and covered by nothing: whoever could delete a row
could also write the record that excused it, and the verifier, even checked
against a head recorded outside the database, answered ``valid``. In the chain,
a removal is covered by every head taken after it, is listed by the verifier
with its reason and time, and under a key (below) cannot be forged by someone
who only has the database.

Three row formats exist, recorded per row in ``chain_v``:

* 1: fields joined with ``|``, SHA-256. Two different rows can share a preimage
  (``a|b`` + ``c`` and ``a`` + ``b|c``). Verified, never written any more.
* 2: canonical JSON array, SHA-256. Detects corruption and unrecorded edits; a
  writer of the database can still recompute it.
* 3: the same array under HMAC-SHA-256 with a key held outside the database
  (``LLM_PROXY_AUDIT_KEY``). A writer of the database without the key cannot
  alter, remove or append rows unnoticed; cutting off the newest rows still
  needs a head recorded elsewhere to show.

The format never goes down along the chain: a row in an older format after a
newer one is a break, so a keyed chain cannot be continued unkeyed.

Both stores share this module so the two backends cannot disagree about what a
valid chain is. It imports the standard library only, so the same code verifies
an exported chain offline (``python -m store.audit_chain``).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import time
from collections.abc import Iterable, Mapping
from typing import Any

#: app_state key the removal records lived under before they moved into the
#: chain. Read once, to carry old records over (see ``legacy_import``).
GAPS_KEY = "audit_chain_gaps"

GENESIS = "GENESIS"

#: Rows the stores read per page while verifying. The whole chain is checked, a
#: page at a time, so memory stays flat however long the log is.
VERIFY_PAGE_SIZE = 5_000

CHAIN_V1 = 1
CHAIN_V2 = 2
CHAIN_V3 = 3

#: Who the chain's own rows belong to. Erasure and export refuse these as
#: subjects: erasing them would delete the record of every earlier deletion.
SYSTEM_SESSION = "AUDIT_SYSTEM"
SYSTEM_KEY_PREFIX = "AUDIT"
RESERVED_SUBJECTS = frozenset({SYSTEM_SESSION, SYSTEM_KEY_PREFIX, "GDPR_SYSTEM", "GDPR"})

EVENT_ROWS_REMOVED = "audit.rows_removed"

#: Removal records per row. An erasure whose rows are scattered through the
#: chain leaves one record per run; they are spread over several rows rather
#: than written as one row of unbounded size.
SEGMENTS_PER_ROW = 500

KEY_ENV = "LLM_PROXY_AUDIT_KEY"
PREVIOUS_KEYS_ENV = "LLM_PROXY_AUDIT_KEY_PREVIOUS"
MIN_KEY_CHARS = 32

_DOMAIN = "llmproxy.audit"


# ── keys ─────────────────────────────────────────────────────────────────────


def keys_from_env(
    environ: Mapping[str, str] | None = None,
) -> tuple[bytes | None, tuple[bytes, ...]]:
    """``(key new rows are sealed with, every key the verifier accepts)``.

    ``LLM_PROXY_AUDIT_KEY`` is the current key; ``LLM_PROXY_AUDIT_KEY_PREVIOUS``
    (comma-separated) keeps rows sealed under retired keys verifiable. A key
    shorter than 32 characters is refused rather than used: a short key makes a
    keyed chain look stronger than it is.
    """
    env = os.environ if environ is None else environ
    current = (env.get(KEY_ENV) or "").strip()
    previous = [k.strip() for k in (env.get(PREVIOUS_KEYS_ENV) or "").split(",") if k.strip()]
    for name, value in [(KEY_ENV, current)] + [(PREVIOUS_KEYS_ENV, k) for k in previous]:
        if value and len(value) < MIN_KEY_CHARS:
            raise ValueError(f"{name} must be at least {MIN_KEY_CHARS} characters")
    write_key = current.encode("utf-8") if current else None
    accepted = ([write_key] if write_key else []) + [k.encode("utf-8") for k in previous]
    return write_key, tuple(accepted)


# ── sealing a row ────────────────────────────────────────────────────────────


def normalise(row: Mapping[str, Any]) -> dict[str, Any]:
    """The audit fields as they are both stored and hashed.

    The value hashed at write time must be the value read back at verify time.
    An integer written to a REAL column comes back a float, a NULL comes back
    None, a bool comes back 0 or 1; fixing the types here, once, on both sides,
    is what keeps a healthy row from failing its own hash.
    """

    def text(value: Any) -> str:
        return "" if value is None else str(value)

    def number(value: Any) -> float:
        try:
            x = float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0
        return x if math.isfinite(x) else 0.0

    def integer(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    return {
        "ts": integer(row.get("ts")),
        "req_id": text(row.get("req_id")),
        "session_id": text(row.get("session_id")),
        "key_prefix": text(row.get("key_prefix")),
        "model": text(row.get("model")),
        "provider": text(row.get("provider")),
        "status": integer(row.get("status")),
        "prompt_tokens": integer(row.get("prompt_tokens")),
        "completion_tokens": integer(row.get("completion_tokens")),
        "cost_usd": number(row.get("cost_usd")),
        "latency_ms": number(row.get("latency_ms")),
        "blocked": 1 if row.get("blocked") else 0,
        "block_reason": text(row.get("block_reason")),
        "metadata": text(row.get("metadata")) or "{}",
    }


def _canonical(prev_hash: str, row: Mapping[str, Any], version: int) -> bytes:
    n = normalise(row)
    return json.dumps(
        [
            _DOMAIN,
            version,
            prev_hash,
            n["ts"],
            n["req_id"],
            n["session_id"],
            n["key_prefix"],
            n["model"],
            n["provider"],
            n["status"],
            n["prompt_tokens"],
            n["completion_tokens"],
            n["cost_usd"],
            n["latency_ms"],
            n["blocked"],
            n["block_reason"],
            n["metadata"],
        ],
        ensure_ascii=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")


def _legacy_payload(prev_hash: str, row: Mapping[str, Any]) -> bytes:
    return (
        f"{prev_hash}|{row['ts']}|{row['req_id']}|{row['session_id']}|"
        f"{row['key_prefix']}|{row['model']}|{row['provider']}|{row['status']}|"
        f"{row['prompt_tokens']}|{row['completion_tokens']}|{row['cost_usd']}|"
        f"{row['latency_ms']}|{row['blocked']}|{row['block_reason']}|{row['metadata']}"
    ).encode()


def entry_hash(
    prev_hash: str,
    row: Mapping[str, Any],
    version: int = CHAIN_V1,
    key: bytes | None = None,
) -> str:
    """The hash a row should carry in ``version``, given the hash before it."""
    if version == CHAIN_V1:
        return hashlib.sha256(_legacy_payload(prev_hash, row)).hexdigest()
    if version == CHAIN_V2:
        return hashlib.sha256(_canonical(prev_hash, row, version)).hexdigest()
    if version == CHAIN_V3:
        if not key:
            raise ValueError("a keyed audit row needs the audit key")
        return hmac.new(key, _canonical(prev_hash, row, version), hashlib.sha256).hexdigest()
    raise ValueError(f"unknown audit chain format {version!r}")


def seal(prev_hash: str, row: Mapping[str, Any], key: bytes | None) -> tuple[int, str]:
    """``(chain_v, entry_hash)`` for a row about to be appended."""
    version = CHAIN_V3 if key else CHAIN_V2
    return version, entry_hash(prev_hash, row, version, key)


def row_version(row: Mapping[str, Any]) -> int:
    """A stored row's format; rows from before the column existed are format 1."""
    try:
        return int(row.get("chain_v") or CHAIN_V1)
    except (TypeError, ValueError):
        return 0


# ── recording a deletion ─────────────────────────────────────────────────────


def load_gaps(raw: Any) -> list[dict[str, Any]]:
    """Removal records from a decoded JSON list (or None)."""
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    if not isinstance(raw, list):
        return []
    return [g for g in raw if isinstance(g, dict) and g.get("start") and g.get("end")]


def segments_from_rows(
    removed: list[dict[str, Any]], *, reason: str, now: float | None = None
) -> list[dict[str, Any]]:
    """Removal records for rows about to be deleted, in chain (id) order.

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

    Kept for the records written to ``app_state`` by earlier releases, which
    were stored merged; new removals are extended with ``extend_back``.
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


def _index_by_end(gaps: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_end: dict[str, list[dict[str, Any]]] = {}
    for g in gaps:
        by_end.setdefault(g["end"], []).append(g)
    # The longest record first: it reaches furthest back in one step.
    for candidates in by_end.values():
        candidates.sort(key=lambda g: int(g.get("rows", 0)), reverse=True)
    return by_end


def extend_back(
    segment: dict[str, Any], known: Iterable[dict[str, Any]]
) -> dict[str, Any]:
    """``segment`` grown backwards over the removals that end where it starts.

    The retention purge removes the oldest rows, among them the rows recording
    earlier purges. Its own record therefore has to stand for everything
    removed before it too, or the first surviving row would link to a removal
    whose record no longer exists.
    """
    by_end = _index_by_end(known)
    out = dict(segment)
    seen = {out["start"]}
    while out["start"] in by_end:
        prior = by_end[out["start"]][0]
        if prior["start"] in seen:
            break
        seen.add(prior["start"])
        out["start"] = prior["start"]
        out["rows"] = int(out.get("rows", 0)) + int(prior.get("rows", 0))
    return out


def removal_metadata(segments: list[dict[str, Any]], *, reason: str) -> list[str]:
    """The ``metadata`` of the row(s) recording ``segments`` as removed."""
    out = []
    for i in range(0, len(segments), SEGMENTS_PER_ROW):
        chunk = [
            {"start": s["start"], "end": s["end"], "rows": int(s.get("rows", 0))}
            for s in segments[i : i + SEGMENTS_PER_ROW]
        ]
        out.append(
            json.dumps(
                {"event": EVENT_ROWS_REMOVED, "reason": reason, "segments": chunk},
                separators=(",", ":"),
                sort_keys=True,
            )
        )
    return out


def removal_row(metadata: str, *, reason: str, now: float | None = None) -> dict[str, Any]:
    """The audit fields of a row recording a removal."""
    return normalise(
        {
            "ts": int(now if now is not None else time.time()),
            "req_id": f"audit-removal-{reason}",
            "session_id": SYSTEM_SESSION,
            "key_prefix": SYSTEM_KEY_PREFIX,
            "status": 200,
            "metadata": metadata,
        }
    )


def is_system_row(row: Mapping[str, Any]) -> bool:
    return row.get("session_id") == SYSTEM_SESSION and row.get("key_prefix") == SYSTEM_KEY_PREFIX


def gaps_from_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The removal records carried by the chain's own rows.

    Each record keeps the id of the row that carries it (``row_id``): the
    verifier accepts a record only once it has verified that row.
    """
    gaps: list[dict[str, Any]] = []
    for row in rows:
        if not is_system_row(row):
            continue
        try:
            meta = json.loads(row.get("metadata") or "{}")
        except ValueError:
            continue
        if not isinstance(meta, dict) or meta.get("event") != EVENT_ROWS_REMOVED:
            continue
        for seg in load_gaps(meta.get("segments")):
            gaps.append(
                {
                    "start": seg["start"],
                    "end": seg["end"],
                    "rows": int(seg.get("rows", 0)),
                    "reason": str(meta.get("reason", "")),
                    "at": int(row.get("ts") or 0),
                    "row_id": row.get("id"),
                }
            )
    return gaps


# ── verification ─────────────────────────────────────────────────────────────


def _path(
    stored_prev: str, expected_prev: str, by_end: dict[str, list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    """The removal records that lead from ``expected_prev`` to ``stored_prev``.

    Empty means no recorded deletion explains the break.
    """
    stack: list[tuple[str, list[dict[str, Any]]]] = [(stored_prev, [])]
    seen: set[str] = set()
    while stack:
        cursor, path = stack.pop()
        if cursor in seen:
            continue
        seen.add(cursor)
        for gap in by_end.get(cursor, ()):
            if gap["start"] == expected_prev:
                return path + [gap]
            stack.append((gap["start"], path + [gap]))
    return []


class ChainVerifier:
    """Checks the chain incrementally: feed pages of rows in ascending id order.

    ``gaps`` are the removal records read from the chain beforehand
    (``gaps_from_rows``); one that bridges a break counts only if the row it
    came from is then met and verified during the walk. ``keys`` are the audit
    keys for format 3 rows. ``anchor`` is a head (``{"id", "hash"}``) recorded
    outside the database.
    """

    def __init__(
        self,
        gaps: list[dict[str, Any]] | None = None,
        anchor: dict[str, Any] | None = None,
        keys: Iterable[bytes] = (),
    ):
        self._by_end = _index_by_end(gaps or [])
        self._keys = tuple(keys)
        self._expected_prev = GENESIS
        self._version = 0
        self.verified = 0
        self.rows_removed = 0
        self.total = 0
        self.formats: dict[int, int] = {}
        # Records used to bridge a break, by the id of the row they came from
        # (None for a record that came from no row), until that row is verified.
        self._unproven: dict[Any, dict[str, Any]] = {}
        self._removals: list[dict[str, Any]] = []
        self._removed_after_anchor = 0
        self._anchor = anchor
        self._first_id: int | None = None
        self._last_id: int | None = None
        self._anchor_hash_seen: str | None = None

    def _broken(self, row: Mapping[str, Any], error: str) -> dict[str, Any]:
        return {
            "valid": False,
            # Rows examined up to and including the one that failed.
            "total": self.total,
            "verified": self.verified,
            "broken_at": row.get("id"),
            "error": error,
        }

    def _hash_matches(self, row: Mapping[str, Any], version: int, stored_prev: str) -> bool:
        stored_hash = row.get("entry_hash", "")
        if version == CHAIN_V3:
            return any(
                hmac.compare_digest(entry_hash(stored_prev, row, version, key), stored_hash)
                for key in self._keys
            )
        return hmac.compare_digest(entry_hash(stored_prev, row, version), stored_hash)

    def _note_removal(self, row: Mapping[str, Any]) -> None:
        """A verified row of the chain's own: the removals it records are proven."""
        row_id = row.get("id")
        self._unproven.pop(row_id, None)
        for gap in gaps_from_rows([row]):
            self._removals.append(
                {
                    "id": row_id,
                    "at": gap["at"],
                    "reason": gap["reason"],
                    "rows": gap["rows"],
                }
            )
            if self._anchor and isinstance(row_id, int) and row_id > self._anchor["id"]:
                self._removed_after_anchor += gap["rows"]

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
                    f"blank entry_hash at id={row_id} after hashed rows (tamper detected)",
                )

            version = row_version(row)
            if version not in (CHAIN_V1, CHAIN_V2, CHAIN_V3):
                return self._broken(row, f"unknown chain format at id={row_id}")
            if version < self._version:
                return self._broken(
                    row,
                    f"chain format goes down at id={row_id} (format {version} after "
                    f"{self._version}): a newer chain was continued in an older format",
                )
            if version == CHAIN_V3 and not self._keys:
                return self._broken(
                    row,
                    f"keyed row at id={row_id} but no audit key is configured "
                    f"({KEY_ENV}): the chain cannot be verified without it",
                )

            if stored_prev != self._expected_prev:
                path = _path(stored_prev, self._expected_prev, self._by_end)
                if not path:
                    return self._broken(row, f"prev_hash mismatch at id={row_id}")
                for gap in path:
                    self.rows_removed += max(int(gap.get("rows", 0)), 0)
                    self._unproven[gap.get("row_id")] = gap
                self.rows_removed = max(self.rows_removed, 1)

            if not self._hash_matches(row, version, stored_prev):
                return self._broken(
                    row, f"entry_hash mismatch at id={row_id} (tamper detected)"
                )

            if is_system_row(row):
                self._note_removal(row)

            self._expected_prev = stored_hash
            self._version = version
            self.formats[version] = self.formats.get(version, 0) + 1
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
            # Every removal the chain records, newest last: what was taken out,
            # when, why, and the id of the row that says so.
            "removals": self._removals[-100:],
            "formats": {f"v{v}": n for v, n in sorted(self.formats.items())},
            "keyed": self._version == CHAIN_V3,
        }
        if self._unproven:
            gap = next(iter(self._unproven.values()))
            result.update(
                valid=False,
                error=(
                    "rows were removed and the record that accounts for it is not "
                    f"a verified row of the chain ({gap.get('rows', '?')} rows, "
                    f"reason={gap.get('reason', '?')})"
                ),
            )
            return result
        if self._anchor is not None:
            status, error = self._check_anchor()
            result["anchor"] = {
                "id": self._anchor["id"],
                "status": status,
                # Rows removed by operations recorded after the anchored row:
                # what has left the log since that head was taken.
                "rows_removed_since": self._removed_after_anchor,
            }
            if error:
                result.update(valid=False, broken_at=self._anchor["id"], error=error)
        return result


def verify_rows(
    rows: list[dict[str, Any]],
    gaps: list[dict[str, Any]] | None = None,
    anchor: dict[str, Any] | None = None,
    keys: Iterable[bytes] = (),
) -> dict[str, Any]:
    """Verify a complete list of rows (ascending id) in one call.

    ``gaps`` defaults to the removal records the rows themselves carry.
    """
    if gaps is None:
        gaps = gaps_from_rows(rows)
    verifier = ChainVerifier(gaps, anchor, keys)
    return verifier.feed(rows) or verifier.result()


def chain_head(last_row: tuple[Any, ...] | None) -> dict[str, Any]:
    """The current head of the chain from ``(id, entry_hash, row_count)`` or None.

    The head is the one short value (id and hash of the newest row, plus the row
    count) worth recording OUTSIDE the database: a log line, a ticket, a monitor.
    Whatever the row format, someone who can write the database can delete the
    newest rows and the chain still verifies against itself; without a key they
    can also rewrite a row and recompute everything after it. Checked against a
    head recorded elsewhere, either shows up (see ChainVerifier's ``anchor``).
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
