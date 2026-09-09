#!/usr/bin/env python3
"""Fail the build when a dependency arrives under a licence this project cannot ship under.

WHY A GATE AND NOT AN INVENTORY
-------------------------------
The audit finding was "no licence inventory tooling". An inventory of the
current tree is a report nobody reads: every one of the 70 locked packages is
MIT, Apache-2.0, BSD, PSF or MPL-2.0 today, so the report would say "fine"
every time until the day it did not, and nobody would be looking.

What actually protects the project is a gate on the transition. llmproxy is
MIT and is distributed as a container image; a transitive dependency arriving
under AGPL-3.0 or SSPL changes what downstream users are allowed to do with
that image, silently, in a Dependabot PR that otherwise looks like a patch
bump. This fails that PR.

WHAT IS DENIED, AND WHY EACH
----------------------------
Strong network copyleft and strong copyleft only:

  AGPL  — the network-use clause reaches users of a HOSTED proxy, which is
          exactly how llmproxy is deployed. This is the one that matters.
  SSPL  — same intent, broader service-source obligation.
  GPL   — v2/v3: distributing the image would carry the obligation to the
          whole work.

NOT denied:

  LGPL  — weak copyleft. Whether a Python import counts as linking is
          genuinely contested, and no LGPL package is in the tree today; if
          one arrives, it should be a human decision rather than a build
          failure with no context. It is reported, not refused.
  MPL-2.0 — file-level copyleft: the obligation covers modifications to the
          MPL-licensed FILES, which this project does not make. certifi,
          hypothesis and pathspec are all MPL-2.0 and all fine.

THE LIMITATION, STATED
----------------------
Packages whose metadata declares no licence are LISTED, not failed on. That is
the hole a bad licence could hide in, and pretending otherwise would be worse
than naming it: failing on unknown metadata makes the gate fire on packaging
sloppiness rather than on licensing risk, and a gate that cries wolf gets
disabled. Read the unknown list when it grows.
"""

from __future__ import annotations

import re
import sys
from importlib.metadata import distributions

#: Substrings that, once normalised, mean strong or network copyleft.
#: Order matters only for readability; every one is checked.
DENIED = ("AGPL", "SSPL", "AFFERO", "SERVER SIDE PUBLIC")

#: Denied unless the match is part of a weaker identifier (LGPL, MPL...).
DENIED_BARE_GPL = re.compile(r"(?<![A-Z])GPL", re.IGNORECASE)

#: Reported, never denied. See the module docstring.
REPORTED = ("LGPL",)


def _identifiers(dist) -> list[str]:
    """Every string in the metadata that could name a licence.

    Three sources because there is no single reliable one: PEP 639's
    License-Expression is new and sparsely adopted, the Classifier list is the
    most consistent in practice, and the free-text License field is what old
    packages fill in.
    """
    meta = dist.metadata
    out: list[str] = []
    for key in ("License-Expression", "License"):
        value = meta.get(key)
        if value and len(value) < 300:  # a full licence text pasted into the field
            out.append(value)
    for classifier in meta.get_all("Classifier") or []:
        if classifier.startswith("License ::"):
            out.append(classifier)
    return out


def classify(identifiers: list[str]) -> str:
    """One of: 'denied', 'reported', 'ok', 'unknown'."""
    if not identifiers:
        return "unknown"
    joined = " ".join(identifiers).upper()
    if any(token in joined for token in DENIED):
        return "denied"
    # "LGPL" contains "GPL"; the lookbehind in DENIED_BARE_GPL excludes it, so
    # check the weak form first and let it win.
    if any(token in joined for token in REPORTED):
        return "reported"
    if DENIED_BARE_GPL.search(joined):
        return "denied"
    return "ok"


def main() -> int:
    denied: list[tuple[str, str]] = []
    reported: list[tuple[str, str]] = []
    unknown: list[str] = []

    for dist in distributions():
        name = dist.metadata.get("Name") or "<unnamed>"
        identifiers = _identifiers(dist)
        verdict = classify(identifiers)
        if verdict == "denied":
            denied.append((name, "; ".join(identifiers)))
        elif verdict == "reported":
            reported.append((name, "; ".join(identifiers)))
        elif verdict == "unknown":
            unknown.append(name)

    if unknown:
        print(f"Licence metadata absent for {len(unknown)} package(s): "
              f"{', '.join(sorted(unknown))}")
        print("  Not a failure — see scripts/check_licences.py for why.\n")

    if reported:
        print("Weak copyleft present (allowed, worth knowing about):")
        for name, ident in sorted(reported):
            print(f"  {name}: {ident}")
        print()

    if denied:
        print("BLOCKED — strong or network copyleft in the dependency tree:")
        for name, ident in sorted(denied):
            print(f"  {name}: {ident}")
        print(
            "\nllmproxy is MIT and ships as a container image users run as a\n"
            "network service. An AGPL/SSPL/GPL dependency changes what those\n"
            "users may do with the image. Remove it, replace it, or make an\n"
            "explicit relicensing decision — not a silent one."
        )
        return 1

    print(f"Licence gate: no strong copyleft in {len(list(distributions()))} packages.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
