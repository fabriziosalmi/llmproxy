"""The release path must depend on the verification path, and say so.

Four separate gaps, all in artefacts nobody runs in the test suite:

  * docker.yml triggered on push, so the image and the moving `latest` tag went
    to the registry in parallel with CI — and a `v*` tag push had no gate at
    all, because branch protection does not cover tags;
  * the Dockerfile's .pth supply-chain scan was a pipeline without pipefail, so
    a failing `find` printed "Clean" and the build carried on;
  * base images were mutable tags, making the largest input to the artefact the
    one nobody pinned;
  * bump_version wrote one of the three files that declare a version, which is
    why ui/package.json sat thirteen minor releases behind.
"""

import json
import os
import re

import pytest

yaml = pytest.importorskip("yaml")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(path: str) -> str:
    with open(os.path.join(_REPO_ROOT, path)) as f:
        return f.read()


def _load_yaml(path: str) -> dict:
    return yaml.safe_load(_read(path))


# ── publishing waits for verification ───────────────────────────────────────


def test_the_image_workflow_is_not_triggered_by_a_push():
    """It fired on push to main AND on v* tags, both ungated."""
    # `on` parses as the boolean True in YAML 1.1 — hence the lookup dance.
    docker = _load_yaml(".github/workflows/docker.yml")
    triggers = docker.get("on", docker.get(True))

    assert "push" not in triggers, (
        "docker.yml triggers on push again — the image would publish in "
        "parallel with CI rather than after it"
    )
    assert "workflow_call" in triggers, "it must be callable from the gated workflow"


def test_ci_publishes_only_behind_the_gates():
    ci = _load_yaml(".github/workflows/ci.yml")
    job = ci["jobs"]["publish-image"]

    assert job["uses"].endswith("docker.yml")
    for gate in ("test", "security", "supply-chain", "invariants"):
        assert gate in job["needs"], f"publishing does not wait for '{gate}'"


def test_ci_runs_on_tags_so_a_tagged_release_is_verified():
    """Branch protection does not cover tag pushes; the suite must."""
    ci = _load_yaml(".github/workflows/ci.yml")
    push = ci.get("on", ci.get(True))["push"]

    assert "tags" in push, "a v* tag push would reach publishing unverified"


def test_every_gate_the_publish_job_names_exists():
    """Guard the guard: a typo in `needs` would silently skip a gate."""
    ci = _load_yaml(".github/workflows/ci.yml")
    jobs = set(ci["jobs"])
    missing = [n for n in ci["jobs"]["publish-image"]["needs"] if n not in jobs]

    assert not missing, f"publish-image needs jobs that do not exist: {missing}"


# ── the supply-chain scan cannot report Clean by accident ───────────────────


def test_the_pth_scan_runs_under_pipefail():
    """Without it the `if` sees grep's status, not find's."""
    dockerfile = _read("Dockerfile")

    assert 'SHELL ["/bin/bash", "-o", "pipefail", "-c"]' in dockerfile
    shell_at = dockerfile.index("pipefail")
    scan_at = dockerfile.index(".pth file audit")
    assert shell_at < scan_at, "pipefail is declared after the scan it protects"


def test_the_pth_scan_validates_where_it_is_looking():
    """An empty SITE_DIR used to produce a confident 'Clean'."""
    dockerfile = _read("Dockerfile")

    assert 'if [ -z "$SITE_DIR" ] || [ ! -d "$SITE_DIR" ]; then' in dockerfile
    assert "could not resolve site-packages" in dockerfile


def test_the_pth_scan_reports_how_many_files_it_examined():
    """A vacuous pass and a real one must not read identically."""
    assert "Examining $PTH_COUNT .pth file(s)" in _read("Dockerfile")


# ── base images are pinned, and something updates the pins ──────────────────


@pytest.mark.parametrize("image", ["python:3.12-slim", "node:20-alpine"])
def test_base_images_are_digest_pinned(image):
    dockerfile = _read("Dockerfile")
    pattern = re.compile(rf"FROM {re.escape(image)}@sha256:[0-9a-f]{{64}}")

    assert pattern.search(dockerfile), (
        f"{image} is referenced by mutable tag — a rebuild of this commit gets "
        "a different foundation"
    )


def test_dependabot_updates_the_digests():
    """Pinning without this trades a moving base for a frozen one."""
    dependabot = _load_yaml(".github/dependabot.yml")
    ecosystems = {u["package-ecosystem"] for u in dependabot["updates"]}

    assert "docker" in ecosystems


# ── secrets and JavaScript advisories are scanned ───────────────────────────


def test_ci_scans_for_committed_secrets():
    """Four live secrets were committed here through .env.example, a tracked
    file that .gitignore cannot protect."""
    ci = _load_yaml(".github/workflows/ci.yml")
    job = ci["jobs"]["secret-scan"]
    uses = " ".join(str(s.get("uses", "")) for s in job["steps"])

    assert "gitleaks" in uses
    assert job["steps"][0]["with"]["fetch-depth"] == 0, "history must be scanned"


def test_the_frontend_audits_production_dependencies():
    """Every npm ci in the repo passes --no-audit and nothing else looked."""
    frontend = _load_yaml(".github/workflows/frontend.yml")
    runs = [
        str(s.get("run", ""))
        for s in frontend["jobs"]["audit"]["steps"]
    ]

    blocking = [r for r in runs if "npm audit" in r and "--omit=dev" in r]
    assert blocking, "no blocking production-scope npm audit"


# ── one version, three files ────────────────────────────────────────────────


def test_the_three_version_declarations_agree():
    """ui/package.json sat at 1.21.80 against a VERSION of 1.34.0."""
    version = _read("VERSION").strip()
    chart = yaml.safe_load(_read("charts/llmproxy/Chart.yaml"))
    ui = json.loads(_read("ui/package.json"))

    assert chart["version"] == version
    assert str(chart["appVersion"]) == version
    assert ui["version"] == version


def test_bump_version_rewrites_all_three(tmp_path, monkeypatch):
    """The script wrote VERSION alone, which is how the other two drifted."""
    import shutil
    import subprocess
    import sys

    for rel in ("VERSION", "charts/llmproxy/Chart.yaml", "ui/package.json"):
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(os.path.join(_REPO_ROOT, rel), dest)
    shutil.copytree(
        os.path.join(_REPO_ROOT, "scripts"), tmp_path / "scripts", dirs_exist_ok=True
    )

    result = subprocess.run(
        [sys.executable, "scripts/bump_version.py", "--minor"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    new = (tmp_path / "VERSION").read_text().strip()
    chart = yaml.safe_load((tmp_path / "charts/llmproxy/Chart.yaml").read_text())
    ui = json.loads((tmp_path / "ui/package.json").read_text())

    assert chart["version"] == new
    assert str(chart["appVersion"]) == new
    assert ui["version"] == new


def test_bump_version_check_reports_drift(tmp_path):
    """--check is what a release runbook can call before tagging."""
    import shutil
    import subprocess
    import sys

    for rel in ("VERSION", "charts/llmproxy/Chart.yaml", "ui/package.json"):
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(os.path.join(_REPO_ROOT, rel), dest)
    shutil.copytree(
        os.path.join(_REPO_ROOT, "scripts"), tmp_path / "scripts", dirs_exist_ok=True
    )
    (tmp_path / "VERSION").write_text("9.9.9")

    result = subprocess.run(
        [sys.executable, "scripts/bump_version.py", "--check"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "disagree" in result.stderr


# ── the watchdog can actually start ─────────────────────────────────────────


def test_the_watchdog_imports_without_undeclared_dependencies():
    """It imported `requests`, declared in neither requirements file, so it
    failed at import on any install from them."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "watchdog_under_test", os.path.join(_REPO_ROOT, "scripts", "watchdog.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # would raise ImportError before

    assert callable(module.is_proxy_alive)


def test_the_watchdog_says_which_deployment_it_is_for():
    source = _read("scripts/watchdog.py")

    assert "bare-metal" in source
    assert "requests" not in source.split('"""')[2], (
        "the stdlib replacement should leave no requests usage in the code"
    )
