"""The licence gate must catch the thing it exists for, and nothing else.

The audit finding was "no licence inventory tooling". An inventory of the
current tree is a report nobody reads — all 70 locked packages are MIT,
Apache-2.0, BSD, PSF or MPL-2.0, so it would say "fine" every time until the
day it did not, with nobody looking. What protects the project is a gate on
the transition: llmproxy is MIT and ships as an image users run as a network
service, so an AGPL or SSPL transitive arriving in a Dependabot patch bump
silently changes what downstream users may do with that image.

These tests pin the classifier, because a gate that mislabels is worse than no
gate: a false negative ships the licence problem, and a false positive gets
the gate deleted.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# scripts/ is a directory of standalone executables, not a package. Loading by
# path keeps it that way rather than adding an __init__.py so a test can import.
_spec = importlib.util.spec_from_file_location(
    "check_licences", ROOT / "scripts" / "check_licences.py"
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
classify = _mod.classify


# ── the licences that must fail the build ───────────────────────────────────


@pytest.mark.parametrize(
    "identifier",
    [
        "AGPL-3.0",
        "GNU Affero General Public License v3",
        "License :: OSI Approved :: GNU Affero General Public License v3",
        "SSPL-1.0",
        "Server Side Public License",
        "GPL-3.0-only",
        "GPLv2",
        "License :: OSI Approved :: GNU General Public License v3 (GPLv3)",
    ],
)
def test_strong_and_network_copyleft_is_denied(identifier):
    assert classify([identifier]) == "denied", identifier


def test_the_agpl_case_the_gate_exists_for():
    """A hosted proxy is precisely what the AGPL network clause reaches."""
    assert classify(["License :: OSI Approved :: GNU Affero General Public License v3 (AGPLv3)"]) == "denied"


# ── the licences that must NOT fail the build ───────────────────────────────


@pytest.mark.parametrize(
    "identifier",
    [
        "MIT",
        "MIT License",
        "License :: OSI Approved :: MIT License",
        "Apache-2.0",
        "Apache Software License",
        "BSD-3-Clause",
        "BSD License",
        "Python Software Foundation License",
        "PSF-2.0",
        "Apache-2.0 OR BSD-3-Clause",
        "Apache-2.0 AND MIT",
    ],
)
def test_permissive_licences_pass(identifier):
    assert classify([identifier]) == "ok", identifier


@pytest.mark.parametrize(
    "identifier",
    ["MPL-2.0", "Mozilla Public License 2.0 (MPL 2.0)"],
)
def test_mpl_passes(identifier):
    """File-level copyleft: the obligation covers modifications to the MPL
    files, which this project does not make. certifi, hypothesis and pathspec
    are all MPL-2.0 and all fine."""
    assert classify([identifier]) == "ok", identifier


# ── the substring trap ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "identifier",
    [
        "LGPL-3.0",
        "GNU Lesser General Public License v3 (LGPLv3)",
        "License :: OSI Approved :: GNU Library or Lesser General Public License (LGPL)",
    ],
)
def test_lgpl_is_reported_not_denied(identifier):
    """"LGPL" contains "GPL". A naive substring match fails the build on weak
    copyleft, which is a contested question in Python (is an import linking?)
    and deserves a human decision rather than a red X with no context."""
    assert classify([identifier]) == "reported", identifier


def test_missing_metadata_is_unknown_not_ok():
    """The one hole the gate does not close is named rather than hidden: a
    package with no licence metadata must not be silently classified as fine."""
    assert classify([]) == "unknown"


# ── the gate actually runs, and passes on this tree ─────────────────────────


def test_the_gate_passes_on_the_current_tree():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_licences.py")],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "no strong copyleft" in result.stdout


def test_ci_runs_the_gate_and_blocks_on_it():
    """A script nobody runs is not a gate."""
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "scripts/check_licences.py" in ci
    # No `continue-on-error` anywhere near it — a non-blocking gate is decoration.
    block = ci[ci.index("Licence gate") : ci.index("Licence gate") + 600]
    assert "continue-on-error" not in block
