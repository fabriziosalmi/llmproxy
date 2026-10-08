"""Documented curl commands carry well-formed headers.

docs/guide/configuration.md once had ``-H "Content-Type": "application/json"``,
with the colon outside the quotes, in the apply step of the one flow that guards
config changes lowering the security posture. Copy-pasted, it fails. This only
checks the shape of -H arguments, which is enough to catch that class of typo.
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOCS = [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]
BAD_HEADER = re.compile(r'-H\s+"[A-Za-z][A-Za-z0-9-]*"\s*:')


def test_no_documented_header_has_its_colon_outside_the_quotes():
    offenders = [
        f"{path.relative_to(ROOT)}:{n}: {line.strip()}"
        for path in DOCS
        if "node_modules" not in path.parts
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if BAD_HEADER.search(line)
    ]
    assert offenders == []
