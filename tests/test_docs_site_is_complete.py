"""Every page the site links to is in the repository, and every page is linked.

The audit-log page was written, built locally and linked from the home page and
the sidebar, and was never published: a `.gitignore` pattern for personal notes
(`AUDIT*.md`) matched `audit-log.md` on a case-insensitive filesystem, so the
file was not committed and the site answered 404 for its headline page. Eight
other pages existed and could not be reached from the navigation at all.
"""

import pathlib
import re
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG = (ROOT / "docs" / ".vitepress" / "config.ts").read_text()
#: Not pages: design notes kept out of the site on purpose (see srcExclude in
#: the config), and the static directory, which is copied as it is.
UNPUBLISHED = ("specs/", "public/")


def _tracked_pages() -> set[str]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "docs"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    pages = set()
    for line in out.splitlines():
        rel = line.removeprefix("docs/")
        if rel.endswith(".md") and not rel.startswith(UNPUBLISHED):
            pages.add("/" + rel.removesuffix(".md"))
    return pages


def _linked() -> set[str]:
    return set(re.findall(r"link:\s*'(/[^'#]*)'", CONFIG))


def test_every_page_in_the_navigation_is_committed():
    missing = sorted(_linked() - _tracked_pages())
    assert missing == [], f"linked from the site but not tracked by git: {missing}"


def test_the_home_page_links_to_committed_pages():
    home = (ROOT / "docs" / "index.md").read_text()
    targets = {t.split("#")[0] for t in re.findall(r"link:\s*(/[^\s]+)", home)}
    assert sorted(targets - _tracked_pages()) == []


def test_every_page_can_be_reached_from_the_navigation():
    orphans = sorted(_tracked_pages() - _linked() - {"/index"})
    assert orphans == [], f"pages that no menu or sidebar links to: {orphans}"


def test_unpublished_notes_are_excluded_from_the_build():
    assert "'specs/**'" in CONFIG
