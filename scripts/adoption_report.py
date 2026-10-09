#!/usr/bin/env python3
"""A snapshot of who is using the project, from numbers GitHub gives for free.

What is measured, and what is not, matters more than the script:

* **Stars, forks, watchers** are weak signals but honest ones.
* **Issues and pull requests from people who are not the owner** (and not bots) are
  the strongest signal available here: someone used it enough to write to you.
* **Clone and view counts are not a usage signal.** Every CI run, every mirror and
  every bot clones; at the time this was written 2,613 clones in 14 days came from
  359 "unique" cloners and 27 unique visitors. They are recorded for reference only.
* **GHCR pulls cannot be read from the REST API.** The package page shows "Total
  downloads" and nothing else does; record it by hand with ``--ghcr-downloads N``.
* Nothing here phones home. There is no telemetry in the proxy and this does not
  add any: it only reads the repository's own public and owner-visible statistics.

Usage:
    python scripts/adoption_report.py                       # print a JSON snapshot
    python scripts/adoption_report.py --append adoption.jsonl --ghcr-downloads 312
    python scripts/adoption_report.py --repo owner/name
    python scripts/adoption_report.py --ignore my-other-account   # your own accounts are not users

Needs the GitHub CLI (``gh``) authenticated with push access for the traffic numbers.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from collections.abc import Callable
from typing import Any

DEFAULT_REPO = "fabriziosalmi/llmproxy"

#: author_association values that mean "someone who works on the project".
_INSIDERS = {"OWNER", "MEMBER", "COLLABORATOR"}


def gh_json(path: str) -> Any:
    out = subprocess.run(
        ["gh", "api", "--paginate", path], capture_output=True, text=True, check=True
    ).stdout
    # --paginate concatenates JSON arrays as ][ ; normalise to one document.
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return [item for chunk in out.replace("][", "]\n[").splitlines() for item in json.loads(chunk)]


def _external(item: dict[str, Any], ignore: frozenset[str] = frozenset()) -> bool:
    user = item.get("user") or {}
    return (
        user.get("login") not in ignore
        and user.get("type") != "Bot"
        and item.get("author_association") not in _INSIDERS
        and not str(user.get("login", "")).endswith("[bot]")
    )


def snapshot(
    repo: str,
    fetch: Callable[[str], Any] = gh_json,
    *,
    today: dt.date | None = None,
    ghcr_downloads: int | None = None,
    ignore: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    info = fetch(f"repos/{repo}")
    issues_and_prs = fetch(f"repos/{repo}/issues?state=all&per_page=100")
    releases = fetch(f"repos/{repo}/releases?per_page=100")
    try:
        clones = fetch(f"repos/{repo}/traffic/clones")
        views = fetch(f"repos/{repo}/traffic/views")
    except subprocess.CalledProcessError:
        clones = views = {}

    outsiders = [i for i in issues_and_prs if _external(i, ignore)]
    external_authors = sorted({i["user"]["login"] for i in outsiders})
    return {
        "date": (today or dt.date.today()).isoformat(),
        "repo": repo,
        "stars": info.get("stargazers_count"),
        "forks": info.get("forks_count"),
        "watchers": info.get("subscribers_count"),
        "external_issues": sum(1 for i in outsiders if "pull_request" not in i),
        "external_pull_requests": sum(1 for i in outsiders if "pull_request" in i),
        "external_authors": len(external_authors),
        "releases": len(releases),
        "release_asset_downloads": sum(
            a.get("download_count", 0) for r in releases for a in r.get("assets", [])
        ),
        # Reference only: dominated by CI and bots (see the module docstring).
        "clones_14d": clones.get("count"),
        "clones_14d_unique": clones.get("uniques"),
        "views_14d": views.get("count"),
        "views_14d_unique": views.get("uniques"),
        # Not available from the API; entered by hand from the package page.
        "ghcr_total_downloads": ghcr_downloads,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--append", metavar="FILE", help="also append the snapshot as a JSON line")
    parser.add_argument(
        "--ignore", action="append", default=[], metavar="LOGIN",
        help="a GitHub login that is you or yours (repeatable): not counted as external",
    )
    parser.add_argument("--ghcr-downloads", type=int, metavar="N", help="'Total downloads' from the GHCR package page")
    args = parser.parse_args(argv)

    try:
        snap = snapshot(
            args.repo, ghcr_downloads=args.ghcr_downloads, ignore=frozenset(args.ignore)
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: could not read the repository statistics: {exc}", file=sys.stderr)
        return 1
    line = json.dumps(snap, sort_keys=True)
    print(json.dumps(snap, indent=2, sort_keys=True))
    if args.append:
        with open(args.append, "a") as f:
            f.write(line + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
