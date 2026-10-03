"""Every background loop is covered by an alert that fits how often it runs.

BackgroundLoopStalled used one 15-minute threshold for every loop, but the
metrics-history, cache-eviction and (new) audit-head loops run hourly and the
retention purge runs daily, so the alert fired for most of every hour on loops
that were working. Slow loops now have their own rules, and this keeps the list
of loops in proxy/background.py and the rules in step.
"""

import pathlib
import re

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
RULES = yaml.safe_load((ROOT / "monitoring" / "prometheus-rules.yml").read_text())

SLOW = {"metrics_history", "cache_eviction", "audit_head", "retention_purge"}


def _alerts():
    return {
        rule["alert"]: " ".join(str(rule["expr"]).split())
        for group in RULES["groups"]
        for rule in group["rules"]
        if "alert" in rule
    }


def _loops_in_code():
    text = (ROOT / "proxy" / "background.py").read_text()
    return set(re.findall(r'_iteration_ok\("([a-z_]+)"\)', text))


def test_the_fast_alert_excludes_exactly_the_slow_loops():
    expr = _alerts()["BackgroundLoopStalled"]
    excluded = set(re.search(r'loop!~"([^"]+)"', expr).group(1).split("|"))

    assert excluded == SLOW


def test_each_slow_loop_has_a_rule_with_a_threshold_longer_than_its_interval():
    alerts = _alerts()
    hourly = alerts["HourlyBackgroundLoopStalled"]
    daily = alerts["RetentionPurgeStalled"]

    for loop in ("metrics_history", "cache_eviction", "audit_head"):
        assert loop in hourly
    assert int(re.search(r"> (\d+)", hourly).group(1)) > 2 * 3600
    assert 'loop="retention_purge"' in daily
    assert int(re.search(r"> (\d+)", daily).group(1)) > 2 * 86400 - 3600


LOOP_FUNCTIONS = {
    "config_watch": "config_watch_loop",
    "write_flush": "write_flush_loop",
    "metrics_history": "metrics_history_loop",
    "cache_eviction": "cache_eviction_loop",
    "dedup_cleanup": "dedup_cleanup_loop",
    "local_discovery": "local_discovery_loop",
    "retention_purge": "retention_purge_loop",
    "audit_head": "audit_head_loop",
    "smart_router_sync": "smart_router_sync_loop",
}


def test_every_reporting_loop_is_known_to_this_test():
    assert _loops_in_code() == set(LOOP_FUNCTIONS), (
        "a loop was added or removed: decide which alert covers it and update LOOP_FUNCTIONS"
    )


def test_loops_are_classified_by_their_real_default_interval():
    """Fast loops must sit well inside the 15-minute rule; slow ones must not."""
    import inspect

    from proxy import background

    for loop, function in LOOP_FUNCTIONS.items():
        interval = inspect.signature(getattr(background, function)).parameters["interval"].default
        if loop in SLOW:
            assert interval >= 3600, f"{loop} runs every {interval}s: it does not need a slow-loop rule"
        else:
            assert interval <= 300, (
                f"{loop} runs every {interval}s: the 15-minute rule would fire for it, "
                "so it needs a slow-loop rule"
            )


def test_the_audit_head_loop_reports_liveness():
    assert "audit_head" in _loops_in_code()
