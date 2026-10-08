"""Revoking proxy-issued sessions before they expire.

POST /api/v1/identity/exchange turns a verified external token into an HS256
proxy JWT (default one hour) that embeds the caller's roles, and verify_proxy_jwt
trusts those roles until the token expires. There was nothing to cut a session
short: a user removed in the identity provider or demoted here kept their roles,
including administrator permissions on the control plane, for the rest of the
hour, and the only lever was rotating LLM_PROXY_IDENTITY_SECRET, which logs out
everyone.

Two ways to revoke, both enforced inside verify_proxy_jwt:

* by ``jti``: one specific token (every proxy JWT now carries a random one);
* by subject: every token for that person issued up to now, the proxy's own
  and the identity provider's (a revoked user's provider token would otherwise
  still authenticate directly, or be exchanged for a fresh session). A token
  issued afterwards, by a new login at the provider, is not affected, so this
  ends sessions without deciding who may log in again; that is the identity
  provider's and the RBAC table's job.

The list is small and in memory, so the check costs a dictionary lookup per
request; it is persisted by the caller (app_state) and reloaded at startup so a
restart does not un-revoke anyone. Entries are dropped once they can no longer
matter.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

#: app_state key the list is persisted under.
STATE_KEY = "identity:revocations"

#: How long a subject-wide revocation is kept. It only has to outlive the
#: longest-lived token issued before it; sessions are an hour by default, so a
#: week is generous, and the list cannot grow without bound.
SUBJECT_RETENTION_S = 7 * 24 * 3600

#: A revoked jti is kept until its token would have expired, plus this slack.
JTI_SLACK_S = 300


class RevocationList:
    def __init__(self, clock: Callable[[], float] = time.time):
        self._clock = clock
        self._jti: dict[str, float] = {}  # jti -> exp of the token
        self._subject: dict[str, int] = {}  # subject -> revoked-at (epoch seconds)

    # ── revoking ──

    def revoke_jti(self, jti: str, exp: float | None = None) -> None:
        """Refuse one token. ``exp`` lets the entry expire with the token."""
        self._jti[jti] = float(exp) if exp else self._clock() + SUBJECT_RETENTION_S

    def revoke_subject(self, subject: str, at: float | None = None) -> int:
        """Refuse every token for ``subject`` issued at or before now."""
        moment = int(self._clock() if at is None else at)
        self._subject[subject] = moment
        return moment

    # ── checking ──

    def is_revoked(self, claims: dict[str, Any]) -> bool:
        jti = claims.get("jti")
        if jti and jti in self._jti:
            return True
        revoked_at = self._subject.get(str(claims.get("sub", "")))
        if revoked_at is None:
            return False
        # iat has one-second resolution, so a token minted in the same second
        # as the revocation is treated as issued before it: the safe side.
        # A token with no iat cannot be shown to postdate the revocation.
        try:
            return int(claims.get("iat", 0)) <= revoked_at
        except (TypeError, ValueError):
            return True

    # ── housekeeping and persistence ──

    def prune(self) -> int:
        now = self._clock()
        stale_jti = [j for j, exp in self._jti.items() if exp + JTI_SLACK_S < now]
        stale_subject = [
            s for s, at in self._subject.items() if at + SUBJECT_RETENTION_S < now
        ]
        for j in stale_jti:
            del self._jti[j]
        for s in stale_subject:
            del self._subject[s]
        return len(stale_jti) + len(stale_subject)

    def dump(self) -> dict[str, Any]:
        self.prune()
        return {"jti": dict(self._jti), "subject": dict(self._subject)}

    def load(self, data: Any) -> None:
        """Replace the list with persisted state; malformed entries are skipped."""
        self._jti, self._subject = {}, {}
        if not isinstance(data, dict):
            return
        jtis, subjects = data.get("jti"), data.get("subject")
        for jti, exp in (jtis.items() if isinstance(jtis, dict) else ()):
            try:
                self._jti[str(jti)] = float(exp)
            except (TypeError, ValueError):
                continue
        for subject, at in (subjects.items() if isinstance(subjects, dict) else ()):
            try:
                self._subject[str(subject)] = int(at)
            except (TypeError, ValueError):
                continue
        self.prune()

    def summary(self) -> dict[str, int]:
        return {"tokens": len(self._jti), "subjects": len(self._subject)}
