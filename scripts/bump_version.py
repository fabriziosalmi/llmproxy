#!/usr/bin/env python3
"""Bump the version everywhere it is declared.

This used to write VERSION and nothing else, so the other two files that carry
a version drifted until someone remembered them by hand. Both did:

  * charts/llmproxy/Chart.yaml once declared 1.33.0 while still deploying the
    1.21.81 image — a defect that outlived eleven releases and is now guarded
    by tests/test_chart_consistency.py;
  * ui/package.json sat at 1.21.80 against a VERSION of 1.34.0, thirteen minor
    releases behind, because nothing ever updated it.

A release script that covers one file of three is a script that trains its
user not to trust it. This one rewrites all three and fails if a file it
expected to change did not, so a silent miss becomes a loud one.

Usage:
    python scripts/bump_version.py            # patch
    python scripts/bump_version.py --minor
    python scripts/bump_version.py --major
    python scripts/bump_version.py --set 1.35.0
    python scripts/bump_version.py --check    # verify the three agree, no writes
"""

import json
import os
import re
import sys

VERSION_FILE = "VERSION"
CHART_FILE = os.path.join("charts", "llmproxy", "Chart.yaml")
UI_PACKAGE_FILE = os.path.join("ui", "package.json")

_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def read_version() -> str:
    with open(VERSION_FILE) as f:
        return f.read().strip()


def read_chart_versions() -> tuple:
    """(version, appVersion) as declared in Chart.yaml."""
    with open(CHART_FILE) as f:
        text = f.read()
    version = re.search(r"^version:\s*(\S+)", text, re.M)
    app_version = re.search(r"^appVersion:\s*\"?([^\"\s]+)\"?", text, re.M)
    return (
        version.group(1) if version else None,
        app_version.group(1) if app_version else None,
    )


def read_ui_version() -> str:
    with open(UI_PACKAGE_FILE) as f:
        return json.load(f).get("version")


def _write_chart(new_version: str) -> bool:
    with open(CHART_FILE) as f:
        text = f.read()
    updated = re.sub(r"^version:\s*\S+", f"version: {new_version}", text, count=1, flags=re.M)
    updated = re.sub(
        r"^appVersion:\s*\"?[^\"\s]+\"?",
        f'appVersion: "{new_version}"',
        updated,
        count=1,
        flags=re.M,
    )
    if updated == text:
        return False
    with open(CHART_FILE, "w") as f:
        f.write(updated)
    return True


def _write_ui(new_version: str) -> bool:
    """Rewrite only the version line — json.dump would reformat the whole file."""
    with open(UI_PACKAGE_FILE) as f:
        text = f.read()
    updated = re.sub(
        r'("version":\s*)"[^"]*"', rf'\1"{new_version}"', text, count=1
    )
    if updated == text:
        return False
    with open(UI_PACKAGE_FILE, "w") as f:
        f.write(updated)
    return True


def check() -> int:
    """Report disagreement between the three declarations. No writes."""
    version = read_version()
    chart_version, chart_app = read_chart_versions()
    ui_version = read_ui_version()

    problems = []
    if chart_version != version:
        problems.append(f"{CHART_FILE} version={chart_version} (VERSION={version})")
    if chart_app != version:
        problems.append(f"{CHART_FILE} appVersion={chart_app} (VERSION={version})")
    if ui_version != version:
        problems.append(f"{UI_PACKAGE_FILE} version={ui_version} (VERSION={version})")

    if problems:
        print("Version declarations disagree:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    print(f"All version declarations agree: {version}")
    return 0


def bump() -> int:
    if "--check" in sys.argv:
        return check()

    if not os.path.exists(VERSION_FILE):
        with open(VERSION_FILE, "w") as f:
            f.write("0.1.0")

    if "--set" in sys.argv:
        new_version = sys.argv[sys.argv.index("--set") + 1]
        if not _SEMVER.match(new_version):
            print(f"Not a semver version: {new_version}", file=sys.stderr)
            return 1
    else:
        major, minor, patch = map(int, read_version().split("."))
        if "--major" in sys.argv:
            major, minor, patch = major + 1, 0, 0
        elif "--minor" in sys.argv:
            minor, patch = minor + 1, 0
        else:
            patch += 1
        new_version = f"{major}.{minor}.{patch}"

    with open(VERSION_FILE, "w") as f:
        f.write(new_version)

    # A file that did not change is a file whose format moved out from under
    # this script. Say so rather than reporting success for one write of three.
    failed = [path for path, ok in (
        (CHART_FILE, _write_chart(new_version)),
        (UI_PACKAGE_FILE, _write_ui(new_version)),
    ) if not ok]
    if failed:
        print(
            f"VERSION is now {new_version}, but these were NOT updated: "
            + ", ".join(failed),
            file=sys.stderr,
        )
        return 1

    print(f"LLMPROXY VERSION: {new_version}")
    print(f"  {VERSION_FILE}, {CHART_FILE} (version + appVersion), {UI_PACKAGE_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(bump())
