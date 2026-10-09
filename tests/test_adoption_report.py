"""The adoption snapshot counts the people who wrote to you, not the bots that cloned.

The point of scripts/adoption_report.py is to give the roadmap's decision gate a number
that means something. Clone counts do not (CI, mirrors and bots dominate them), so
what is tested is that outsiders are told apart from the owner, collaborators and bots,
that pull requests and issues are counted separately, and that a missing traffic
permission degrades to nulls instead of failing.
"""

import datetime as dt
import importlib.util
import pathlib
import subprocess

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "adoption_report.py"
spec = importlib.util.spec_from_file_location("adoption_report", SCRIPT)
adoption = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adoption)


def _item(login, association="NONE", kind="User", pr=False):
    item = {"user": {"login": login, "type": kind}, "author_association": association}
    if pr:
        item["pull_request"] = {}
    return item


ISSUES = [
    _item("fabriziosalmi", "OWNER"),
    _item("fabriziosalmi", "OWNER", pr=True),
    _item("dependabot[bot]", "NONE", kind="Bot", pr=True),
    _item("github-actions[bot]", "NONE", kind="Bot"),
    _item("alice"),
    _item("alice", pr=True),
    _item("bob", "CONTRIBUTOR", pr=True),
    _item("carol", "FIRST_TIME_CONTRIBUTOR"),
    _item("teammate", "COLLABORATOR"),
]


def _fetch(traffic=True):
    def fetch(path):
        if path.endswith("/traffic/clones") or path.endswith("/traffic/views"):
            if not traffic:
                raise subprocess.CalledProcessError(403, "gh")
            return {"count": 2613, "uniques": 359} if "clones" in path else {"count": 100, "uniques": 27}
        if "/issues" in path:
            return ISSUES
        if "/releases" in path:
            return [{"assets": [{"download_count": 3}, {"download_count": 4}]}, {"assets": []}]
        return {"stargazers_count": 12, "forks_count": 4, "subscribers_count": 1}

    return fetch


def test_outsiders_are_separated_from_the_owner_collaborators_and_bots():
    snap = adoption.snapshot("o/r", _fetch(), today=dt.date(2026, 10, 9))

    assert snap["external_issues"] == 2          # alice, carol
    assert snap["external_pull_requests"] == 2   # alice, bob
    assert snap["external_authors"] == 3         # alice, bob, carol


def test_the_basic_counts_and_the_date():
    snap = adoption.snapshot("o/r", _fetch(), today=dt.date(2026, 10, 9), ghcr_downloads=312)

    assert (snap["stars"], snap["forks"], snap["watchers"]) == (12, 4, 1)
    assert snap["releases"] == 2 and snap["release_asset_downloads"] == 7
    assert snap["date"] == "2026-10-09" and snap["ghcr_total_downloads"] == 312


def test_without_traffic_permission_the_traffic_fields_are_null_not_an_error():
    snap = adoption.snapshot("o/r", _fetch(traffic=False))

    assert snap["clones_14d"] is None and snap["views_14d_unique"] is None
    assert snap["stars"] == 12


def test_the_ghcr_number_is_never_invented():
    assert adoption.snapshot("o/r", _fetch())["ghcr_total_downloads"] is None


def test_your_own_other_accounts_can_be_excluded():
    snap = adoption.snapshot("o/r", _fetch(), ignore=frozenset({"alice"}))

    assert snap["external_authors"] == 2          # bob, carol
    assert snap["external_issues"] == 1           # carol
    assert snap["external_pull_requests"] == 1    # bob
