"""The Helm chart must mount a volume for data/.

The chart's deployment mounted only the config ConfigMap. There was no PVC
template, no persistence values and no volumeMount for /app/data, so a
Helm-deployed pod wrote the endpoint registry, app_state, the spend ledger, the
audit chain and the encryption salt into its ephemeral filesystem and lost all
of it on every restart, rescheduling or `helm upgrade`.

That is the same failure the CHANGELOG records as a production incident — the
database opened in the container's writable layer, wiped on every restart. It
was fixed for the systemd and Compose paths by moving the store under data/;
the chart never received a volume, so it shipped the identical defect.

These parse the templates as text rather than rendering with helm, so they run
in CI without the binary. test_chart_consistency.py covers the rest of the
chart's invariants.
"""

import pathlib

import pytest
import yaml

CHART = pathlib.Path(__file__).parent.parent / "charts" / "llmproxy"


@pytest.fixture(scope="module")
def values() -> dict:
    return yaml.safe_load((CHART / "values.yaml").read_text())


@pytest.fixture(scope="module")
def deployment_src() -> str:
    return (CHART / "templates" / "deployment.yaml").read_text()


def test_a_pvc_template_exists():
    pvc = CHART / "templates" / "pvc.yaml"
    assert pvc.exists(), "the chart has no PersistentVolumeClaim template"
    assert "kind: PersistentVolumeClaim" in pvc.read_text()


def test_persistence_is_on_by_default(values):
    """An audit chain that restarts empty on every deploy evidences nothing."""
    persistence = values.get("persistence")
    assert persistence is not None, "values.yaml declares no persistence block"
    assert persistence["enabled"] is True


def test_the_claim_is_sized_and_its_access_mode_matches_one_replica(values):
    persistence = values["persistence"]
    assert persistence["size"], "the claim has no size"
    # replicaCount is pinned to 1 for state reasons, so a single writer is
    # correct — ReadWriteMany would imply a topology the app cannot support.
    assert persistence["accessModes"] == ["ReadWriteOnce"]


def test_the_deployment_mounts_the_volume_at_the_data_directory(deployment_src):
    assert "mountPath: /app/data" in deployment_src, (
        "the deployment does not mount a volume at /app/data — the database, "
        "audit chain and encryption salt are on the pod's ephemeral filesystem"
    )
    assert "persistentVolumeClaim:" in deployment_src


def test_the_mount_is_gated_on_the_persistence_toggle(deployment_src):
    """Disabling persistence must remove the mount, not leave a dangling ref."""
    assert deployment_src.count("{{- if .Values.persistence.enabled }}") >= 2


def test_an_existing_claim_can_be_bound_instead(deployment_src):
    """Restoring a snapshot means pointing at a claim the chart did not create."""
    assert ".Values.persistence.existingClaim" in deployment_src


def test_replica_count_is_still_pinned_to_one(values):
    """The volume is ReadWriteOnce; that only holds while this stays 1."""
    assert values["replicaCount"] == 1
    assert values["autoscaling"]["enabled"] is False
