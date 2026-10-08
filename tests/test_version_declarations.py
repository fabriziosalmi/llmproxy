"""Every place that declares or pins the release agrees with VERSION.

VERSION, the chart, ui/package.json and (now) the image tags the README and the
deployment guide tell operators to pull. The last two had drifted: README pinned
:1.35.0 and the guide :1.33.0 against 1.37.19, and the 1.33.0 image predates the
admin-key tier the same guide tells operators to configure.
"""

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_all_version_declarations_and_doc_pins_agree():
    result = subprocess.run(
        [sys.executable, "scripts/bump_version.py", "--check"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
