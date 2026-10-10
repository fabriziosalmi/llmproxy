# Audit Log

Every request that reaches the pipeline is recorded as a row whose hash covers the
row before it. This page says what is recorded, how to check the log, and what a
successful check does and does not show.

## Try it

With the proxy running and an admin key:

```bash
ADMIN="Authorization: Bearer $LLM_PROXY_ADMIN_KEY"

# 1. The head of the chain: the id and hash of its newest row.
curl -s http://localhost:8090/api/v1/audit/head -H "$ADMIN"
# {"id":1,"hash":"66fd79ce...","count":1}

# 2. Verify the whole chain, and that it still contains that head.
curl -s "http://localhost:8090/api/v1/audit/verify?anchor_id=1&anchor_hash=66fd79ce..." -H "$ADMIN"
# {"valid":true,"total":1,"verified":1,...,"anchor":{"id":1,"status":"ok","rows_removed_since":0}}

# 3. Edit a row behind the proxy's back, then verify again.
docker exec llmproxy python -c "import sqlite3; c = sqlite3.connect('/app/data/endpoints.db'); c.execute('UPDATE audit_log SET status = 200 WHERE id = 1'); c.commit()"
curl -s "http://localhost:8090/api/v1/audit/verify?anchor_id=1&anchor_hash=66fd79ce..." -H "$ADMIN"
# {"valid":false,"total":1,"verified":0,"broken_at":1,"error":"entry_hash mismatch at id=1 (tamper detected)"}
```

## What is recorded

| In the chain | Not in the chain |
|---|---|
| Chat, completion and embedding requests that reach the pipeline | Prompts and responses: the log holds metadata, not content |
| Requests the shield, a plugin or the tool policy refused (`blocked = 1`, with the reason the caller was given) | Requests rejected before the pipeline: a missing or wrong key, the rate limiter, the byte firewall |
| Requests that failed upstream (their `5xx`) | Control-plane changes: configuration, feature toggles, plugin installs |
| Streams, with how they ended (completed, cut by a guard, upstream error, client disconnect) | |
| Retention purges and erasures, as removal records | |
| GDPR export and erasure requests | |

A row carries: time, request id, session id, caller, model, provider, status,
prompt and completion tokens, cost, latency, the blocked flag and reason, and a
small `metadata` object naming the kind of event.

**The caller** is the first eight characters of the API key, or the signed-in
user's email (or subject) for an identity token. Keys that share their first eight
characters are not told apart. The session id is an HMAC of the credential; it is
the same across restarts only when `LLM_PROXY_IDENTITY_SECRET` is set.

**When it is written.** After the response, from a queue. If the store falls
behind, requests beyond `audit.max_pending_writes` (default 1000) wait for their
own row. A write that fails is logged and counted
(`llm_proxy_audit_persistence_total{outcome="fail"}`) and the request is still
served; rows still queued when the process is killed are lost. The log is a record
of what the proxy handled, not a gate that requests must pass.

## Reading it

```
GET /api/v1/audit?from=2026-10-01T00:00:00Z&to=2026-10-02T00:00:00Z&blocked=1&limit=100
```

Filters: `from`, `to` (ISO 8601), `model`, `key_prefix`, `status`, `blocked` (`0` or
`1`), `limit`, `offset`. Permission `logs:read`. See the
[API reference](/api/admin#audit-integrity).

## Verifying it

```
GET /api/v1/audit/verify
GET /api/v1/audit/verify?anchor_id=<id>&anchor_hash=<hash>
GET /api/v1/audit/head
```

`verify` walks every row in order and recomputes its hash. The answer:

| Field | Meaning |
|---|---|
| `valid` | `false` when a row was altered, a link is broken, or a removal is not accounted for |
| `broken_at`, `error` | The row where it failed, and why |
| `total`, `verified` | Rows examined and rows that verified, including the chain's own removal records |
| `rows_removed`, `removals` | Rows removed by retention or erasure, and each removal with its time, reason and row count |
| `formats`, `keyed` | Rows per chain format, and whether the newest rows are sealed with a key |
| `anchor` | With an anchor: `status` and `rows_removed_since` |

**The head and the anchor.** `head` returns the newest row's id and hash. Recorded
somewhere the database's writers cannot reach, it is an anchor: `verify` then also
checks that the chain still contains that row with that hash.

| Anchor status | Meaning |
|---|---|
| `ok` | The row is there and unchanged |
| `purged` | The row is older than the oldest retained row: removed by retention |
| `erased` | The row was removed by a recorded erasure |
| `mismatch` | The row is there with another hash: rows up to it were rewritten |
| `truncated` | The chain now ends before that row |
| `missing` | The row is gone and no recorded removal accounts for it |

The first three are consistent with a healthy log; the last three make `valid`
false. The proxy writes the head to its process log (standard error) every hour as
`AUDIT HEAD id=... hash=... count=...` (`audit.head_log_interval_seconds`; `0`
turns it off). That line is outside the database only once the log has left the
host.

## The key

Without a key, a row's hash is SHA-256 over a canonical encoding of its fields.
That detects accidental damage and an edit like the one above. It does not stop
someone who can write the database from rewriting a row and recomputing every hash
after it, or from appending a well-formed removal record for rows they deleted.

Set `LLM_PROXY_AUDIT_KEY` (32 characters or more) and rows are sealed with
HMAC-SHA-256. Someone who can write the database, or a backup of it, and does not
hold the key can then not alter, remove or add a row without `verify` failing.

- Keep the key where the database's writers cannot read it. In the same place, it
  adds nothing.
- Keep a copy off the host. Without the key the keyed rows cannot be verified.
- Once a keyed row exists, an unkeyed row after it is a break: the key cannot be
  removed without `verify` failing. That is deliberate.
- To rotate, set the new key and move the old one to
  `LLM_PROXY_AUDIT_KEY_PREVIOUS` (comma-separated) until the rows sealed with it
  have left the retention window.

Cutting off the newest rows is the one change a key does not reveal on its own:
the chain that remains still verifies. An anchor does reveal it.

## Retention and erasure

Rows older than `gdpr.retention_days` (default 90) are purged daily when
`gdpr.auto_purge` is on (the default); the first automatic purge runs 24 hours
after start. `POST /api/v1/gdpr/purge` does it now.

`POST /api/v1/gdpr/erase/{subject}` removes a subject's rows. The subject is
matched exactly against the session id or the caller, must be at least eight
characters, and cannot be one of the names the chain files its own rows under. The
request is recorded before anything is deleted, with the SHA-256 of the subject
rather than the subject.

Both operations delete rows from the middle or the start of the chain. Each
appends a removal record: the hash before the removed run and the hash of its last
row, no content. `verify` bridges a gap only when such a record, itself a verified
row, accounts for it, and lists every removal.

## Limits

- **Metadata only.** The log shows that a request happened, from whom, to which
  model, with what outcome. It does not show what was said.
- **Not everything is in it.** See the table above. In particular a change to the
  configuration, or switching a guard off, is not a row.
- **The process is trusted.** Whoever can run code as the proxy, or read its
  environment, holds the key. The chain is evidence against the database and its
  backups being edited, not against the host.
- **A rollback of the whole database** to an earlier state matches an anchor taken
  at that time.
- **The chain format is one-way.** Releases before 1.38.0 report a chain written
  by this one as broken.
- **Verification reads every row.** Its duration grows with the length of the log.
- **No offline verifier.** `store/audit_chain.py` uses the standard library only so
  that one can be built; there is no export or command for it yet.

The reasoning behind these, and the rest of the model, is in the
[threat model](/security/threat-model).
