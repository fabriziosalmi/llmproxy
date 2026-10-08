"""CI, `make typecheck` and the pre-commit hook run the same mypy check.

`make typecheck` omitted the three --disable-error-code flags CI passed, so on a
clean main it printed "Found 23 errors" while CI reported success, and the pre-commit
hook had its own copy that also skipped store/ and plugins/. The flags now live in
mypy.ini, the one place all three read.
"""

import configparser
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
RUNNERS = [ROOT / ".github" / "workflows" / "ci.yml", ROOT / "Makefile", ROOT / "scripts" / "pre-commit.sh"]


def test_the_ignored_error_codes_are_defined_once_in_mypy_ini():
    cfg = configparser.ConfigParser()
    cfg.read(ROOT / "mypy.ini")
    codes = {c.strip() for c in cfg["mypy"]["disable_error_code"].split(",")}
    assert codes == {"misc", "assignment", "no-any-return"}


def test_no_runner_carries_its_own_copy():
    for path in RUNNERS:
        assert "disable-error-code" not in path.read_text(), path.name


def test_every_runner_checks_the_same_packages():
    for path in RUNNERS:
        text = path.read_text()
        line = next(ln for ln in text.splitlines() if "mypy core/" in ln)
        for package in ("core/", "proxy/", "store/", "plugins/"):
            assert package in line, (path.name, package)
