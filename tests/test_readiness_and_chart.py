"""Readiness can fail, and the Helm chart rolls out safely.

* ``/health`` is 200 whatever it finds (its verdict is in the body), so the
  chart's readiness probe, an httpGet status check, could never mark a pod
  unready. ``/ready`` carries the same verdict as a status code.
* The chart used the RollingUpdate default on a ReadWriteOnce volume holding a
  single SQLite writer: two writers on one node, a Multi-Attach deadlock on
  several, and `values.yaml` said it could not happen.
* config.yaml arrives through a subPath mount, which Kubernetes never refreshes;
  with no checksum annotation a config-only `helm upgrade` changed nothing.
"""

import shutil
import subprocess

import pytest
import yaml
from httpx import ASGITransport, AsyncClient

from tests.test_coverage_routes import _make_app_with_routes

helm = pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")


# ── /ready ────────────────────────────────────────────────────────────────────


async def _get(path, mutate=None):
    from proxy.routes.telemetry import create_router

    app, agent = _make_app_with_routes(create_router)
    if mutate:
        mutate(agent)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return await c.get(path)


async def test_ready_is_200_when_the_verdict_is_ok():
    resp = await _get("/ready")

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_ready_is_503_when_the_verdict_is_down_but_health_stays_200():
    class _Closed:
        closed = True

    def close_session(agent):
        agent._session = _Closed()

    ready = await _get("/ready", close_session)
    health = await _get("/health", close_session)

    assert ready.status_code == 503
    assert ready.json()["status"] == "down"
    assert health.status_code == 200  # pollers that read the body are unaffected
    assert health.json()["status"] == "down"


def test_ready_is_reachable_without_credentials_like_health():
    from proxy.app_factory import _PUBLIC_EXACT

    assert {"/health", "/ready"} <= _PUBLIC_EXACT


# ── the chart ─────────────────────────────────────────────────────────────────


def _render(*extra):
    out = subprocess.run(
        ["helm", "template", "t", "charts/llmproxy", *extra],
        capture_output=True, text=True, check=True,
    ).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def _deployment(docs):
    return next(d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"].endswith("llmproxy"))


@helm
def test_the_deployment_recreates_rather_than_rolls():
    deployment = _deployment(_render())

    assert deployment["spec"]["strategy"] == {"type": "Recreate"}


@helm
def test_readiness_uses_ready_and_liveness_keeps_health():
    container = _deployment(_render())["spec"]["template"]["spec"]["containers"][0]

    assert container["readinessProbe"]["httpGet"]["path"] == "/ready"
    assert container["livenessProbe"]["httpGet"]["path"] == "/health"


@helm
def test_a_config_change_changes_the_pod_template():
    def checksum(config):
        deployment = _deployment(_render("--set-string", f"config={config}"))
        return deployment["spec"]["template"]["metadata"]["annotations"]["checksum/config"]

    assert checksum("server:\n  port: 8090\n") == checksum("server:\n  port: 8090\n")
    assert checksum("server:\n  port: 8090\n") != checksum("server:\n  port: 9000\n")


@helm
def test_pod_annotations_still_render_beside_the_checksum():
    deployment = _deployment(_render("--set", "podAnnotations.team=platform"))
    annotations = deployment["spec"]["template"]["metadata"]["annotations"]

    assert annotations["team"] == "platform" and "checksum/config" in annotations
