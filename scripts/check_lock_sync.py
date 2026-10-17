#!/usr/bin/env python3
"""Fail when requirements.txt (the lock) is not the resolution of requirements.in.

Usage: check_lock_sync.py <committed lock> <freshly compiled lock>

WHY COMPARE CONTENT, NOT TEXT
-----------------------------
The lockfile job used to recompile requirements.in in place and run
`git diff --exit-code`. That treats formatting as meaning. Dependabot
regenerates the lock with its own uv, which writes the same resolution
differently: it keeps extras on the pin (`sentry-sdk[fastapi]==2.61.1` where
ours writes `sentry-sdk==2.61.1`) and lists fewer `# via` comments. Every pip
pull request it opened (#223, #230) failed the job with identical versions and
identical hashes.

What the lock promises is a set of packages, each at one version, each with
the hashes pip will accept. That is what is compared here: name (normalised,
extras dropped), version, and the set of hashes. Comments, extras, ordering
and line wrapping are not compared.

The caller still compiles IN PLACE, so uv keeps the committed versions as
preferences: compiled into an empty file, every open-ended range would resolve
to whatever is newest today, and the check would fail on any upstream release.
"""

import re
import sys
from pathlib import Path

PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(\[[^\]]*\])?==([^\s;\\]+)")
HASH = re.compile(r"--hash=(sha256:[0-9a-f]{64})")


def normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse(path: str) -> dict[str, tuple[str, frozenset[str]]]:
    """{name: (version, hashes)} from a hash-pinned requirements file."""
    # Join backslash continuations so each requirement is one logical line.
    logical = re.sub(r"\\\n", " ", Path(path).read_text())
    pins: dict[str, tuple[str, frozenset[str]]] = {}
    for line in logical.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = PIN.match(line)
        if not m:
            sys.exit(f"{path}: not a pinned requirement: {line[:80]}")
        pins[normalise(m.group(1))] = (m.group(3), frozenset(HASH.findall(line)))
    if not pins:
        # Two empty files agree with each other; that is not a lock in sync.
        sys.exit(f"{path}: no pinned requirements found")
    return pins


def main() -> int:
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    committed, fresh = parse(sys.argv[1]), parse(sys.argv[2])
    problems = []
    for name in sorted(committed.keys() | fresh.keys()):
        if name not in fresh:
            problems.append(f"{name}: in the lock, no longer in the resolution")
        elif name not in committed:
            problems.append(f"{name}: in the resolution, missing from the lock")
        elif committed[name][0] != fresh[name][0]:
            problems.append(f"{name}: lock has {committed[name][0]}, resolution has {fresh[name][0]}")
        elif committed[name][1] != fresh[name][1]:
            problems.append(f"{name}=={committed[name][0]}: the hashes differ")
    if problems:
        print("requirements.txt is out of sync with requirements.in:")
        print("\n".join(f"  {p}" for p in problems))
        return 1
    print(f"OK: requirements.txt is the resolution of requirements.in ({len(committed)} packages)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
