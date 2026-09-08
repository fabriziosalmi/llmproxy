"""The Helm chart must deploy the version it claims to be.

charts/llmproxy/Chart.yaml declared version and appVersion 1.33.0 while
values.yaml pinned image.tag to "1.21.81". Helm honours the explicit value, so
`helm install` from a chart reporting itself as 1.33.0 deployed an image eleven
releases old — one that still wrote its database into an ephemeral container
layer, the defect that release was cut to fix. The two values had been
consistent; the chart version was bumped and the image tag was not moved with
it, so the drift was introduced by the bump itself.
"""

import pathlib

import pytest

yaml = pytest.importorskip("yaml")

CHART_DIR = pathlib.Path(__file__).resolve().parent.parent / "charts" / "llmproxy"


@pytest.fixture(scope="module")
def chart():
    return yaml.safe_load((CHART_DIR / "Chart.yaml").read_text())


@pytest.fixture(scope="module")
def values():
    return yaml.safe_load((CHART_DIR / "values.yaml").read_text())


def test_chart_appversion_matches_the_project_version(chart):
    version_file = (CHART_DIR.parent.parent / "VERSION").read_text().strip()
    assert str(chart["appVersion"]) == version_file, (
        f"chart appVersion {chart['appVersion']} != VERSION {version_file}; "
        "a release bump must move both"
    )


def test_image_tag_does_not_pin_a_stale_version(chart, values):
    """Empty means follow appVersion, which the template already does.

    An explicit tag is allowed only if it matches appVersion — otherwise the
    chart deploys something other than what it says it is.
    """
    tag = values["image"].get("tag") or ""
    assert tag == "" or tag == str(chart["appVersion"]), (
        f"values.yaml pins image.tag to {tag!r} while the chart declares "
        f"appVersion {chart['appVersion']!r}. Leave the tag empty so the "
        "deployment template's `default .Chart.AppVersion` applies."
    )


def test_deployment_template_falls_back_to_appversion():
    """The empty tag only works because the template defaults to appVersion."""
    template = (CHART_DIR / "templates" / "deployment.yaml").read_text()
    assert ".Values.image.tag | default .Chart.AppVersion" in template, (
        "the deployment template no longer falls back to appVersion, so an "
        "empty image.tag would render an invalid image reference"
    )
