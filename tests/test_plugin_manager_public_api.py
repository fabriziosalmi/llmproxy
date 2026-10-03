"""The transport layer reads PluginManager through its public API.

The admin metrics route, request_pipeline and the shutdown handler used to read
PluginManager._ring_traces, ._percentiles, ._ring_traces_index and
._plugin_instances directly. Those are attribute accesses, so no import or type
check flagged them, and restructuring the manager's internals would have broken
three modules at runtime.
"""

import pathlib
import re

import pytest

from core.plugin_engine import PluginManager

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _manager(tmp_path):
    return PluginManager(plugins_dir=str(tmp_path))


def _trace(pm, req_id, **fields):
    trace = {"req_id": req_id, "rings": {}, **fields}
    pm._ring_traces.append(trace)
    pm._ring_traces_index[req_id] = trace
    return trace


def test_ttft_stats_summarise_only_traces_that_carry_a_ttft(tmp_path):
    pm = _manager(tmp_path)
    for i, ttft in enumerate([100.0, 200.0, 300.0, 400.0]):
        _trace(pm, f"r{i}", ttft_ms=ttft)
    _trace(pm, "no-ttft")

    stats = pm.get_ttft_stats()

    assert stats["samples"] == 4
    assert stats["p50"] == 300.0
    assert set(stats) == {"samples", "p50", "p95", "p99"}


def test_ttft_stats_are_zero_without_samples(tmp_path):
    assert _manager(tmp_path).get_ttft_stats() == {
        "samples": 0, "p50": 0, "p95": 0, "p99": 0
    }


def test_annotate_ring_trace_adds_fields_to_an_existing_trace(tmp_path):
    pm = _manager(tmp_path)
    trace = _trace(pm, "r1")

    assert pm.annotate_ring_trace("r1", total_ms=12.5, upstream_ms=10.0) is True

    assert trace["total_ms"] == 12.5 and trace["upstream_ms"] == 10.0


def test_annotate_ring_trace_reports_a_missing_trace(tmp_path):
    assert _manager(tmp_path).annotate_ring_trace("gone", total_ms=1.0) is False


class _Plugin:
    def __init__(self, fail=False):
        self.fail, self.unloaded = fail, False

    async def on_unload(self):
        self.unloaded = True
        if self.fail:
            raise RuntimeError("cannot flush")


async def test_unload_all_calls_every_plugin_even_when_one_fails(tmp_path, caplog):
    pm = _manager(tmp_path)
    first, broken, last = _Plugin(), _Plugin(fail=True), _Plugin()
    pm._plugin_instances = {"first": first, "broken": broken, "last": last}

    await pm.unload_all()

    assert first.unloaded and broken.unloaded and last.unloaded
    assert "Plugin 'broken' unload failed" in caplog.text


@pytest.mark.parametrize("subdir", ["proxy"])
def test_the_transport_layer_does_not_reach_into_plugin_manager_internals(subdir):
    pattern = re.compile(r"plugin_manager\._[a-z]")
    offenders = [
        f"{path.relative_to(ROOT)}:{n}: {line.strip()}"
        for path in (ROOT / subdir).rglob("*.py")
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if pattern.search(line)
    ]
    assert not offenders, "private PluginManager access:\n" + "\n".join(offenders)
