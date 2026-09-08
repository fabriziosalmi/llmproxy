"""The proxy version — one reader, one fallback.

VERSION was read in three places with three different answers when the file
was missing: "0.0.0" in the app factory, "unknown" in the webhook SIEM
records, and "0.1.0-alpha" on /api/v1/version — a release that has never
existed. An operator asking the running proxy what it was would be told a
plausible, wrong number, and a fleet inventory built on that endpoint would
group every version-less deployment under an invented release.

The fallback is "unknown" everywhere now: not a version, and not mistakable
for one.
"""

import os
from functools import lru_cache

_VERSION_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "VERSION"
)

UNKNOWN = "unknown"


@lru_cache(maxsize=1)
def get_version() -> str:
    """The contents of VERSION, or "unknown". Never a fabricated number."""
    try:
        with open(_VERSION_FILE) as f:
            return f.read().strip() or UNKNOWN
    except OSError:
        return UNKNOWN
