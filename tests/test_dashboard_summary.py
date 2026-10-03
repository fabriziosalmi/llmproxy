"""GET /api/v1/dashboard/summary: what it reports, section by section.

The handler was one 230-line closure (cyclomatic complexity 52) that gathered
state, built five kinds of attention item, sorted them, mapped them to tasks and
read the audit log, with a try/except around each section. These tests pin what
it returns so it can be taken apart (proxy/dashboard.py) without changing it,
and pin the property the try/excepts exist for: a broken section must not take
the dashboard down.
"""

import time
from unittest.mock import AsyncMock, MagicMock

import httpx
from fastapi import FastAPI

from tests.conftest import minimal_config


def _endpoint(id_):
    ep = MagicMock()
    ep.id = id_
    return ep


class _Ledger:
    threshold = 3.0

    def __init__(self, ips=None, keys=None):
        self._ip_ledger = ips or {}
        self._key_ledger = keys or {}


def _agent(
    *,
    pool=("a", "b"),
    circuits=None,
    executable=None,
    ledger=None,
    daily=50.0,
    soft=40.0,
    spent=0.0,
    audit_items=(),
):
    agent = MagicMock()
    agent.config = minimal_config(auth_enabled=False)
    agent.config["budget"] = {"daily_limit": daily, "soft_limit": soft}
    agent._start_time = time.time() - 100
    agent.total_cost_today = spent
    endpoints = [_endpoint(i) for i in pool]
    agent.store.get_pool = AsyncMock(return_value=endpoints)
    agent.store.query_audit = AsyncMock(return_value={"items": list(audit_items)})
    agent.circuit_manager.get_all_states = AsyncMock(return_value=circuits or {})
    agent.circuit_manager.filter_executable = AsyncMock(
        return_value=list(pool) if executable is None else list(executable)
    )
    agent.security.threat_ledger = ledger
    return agent


async def _summary(agent):
    from proxy.routes.admin import create_router

    app = FastAPI()
    app.include_router(create_router(agent))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as c:
        return await c.get("/api/v1/dashboard/summary")


def _kinds(body):
    return [a["kind"] for a in body["attention"]]


# ── now ─────────────────────────────────────────────────────────────────────


async def test_a_quiet_system_is_nominal_with_default_tasks_and_a_placeholder():
    resp = await _summary(_agent())

    assert resp.status_code == 200
    body = resp.json()
    now = body["now"]
    assert now["health"] == "nominal" and now["degradation_state"] == "nominal"
    assert now["pool_size"] == 2 and now["pool_healthy"] == 2
    assert now["auth_mode"] == "disabled"
    assert isinstance(now["throughput_today"], int)
    assert 99 <= now["uptime_seconds"] <= 110
    assert body["attention"] == []
    assert [t["id"] for t in body["do_next"]] == ["task:review_budget", "task:check_plugins"]
    assert [c["type"] for c in body["recent_changes"]] == ["system_boot"]


async def test_an_open_circuit_degrades_and_one_with_nothing_executable_is_critical():
    circuits = {"a": {"state": "open", "last_state_change": time.time() - 500}}
    degraded = (await _summary(_agent(circuits=circuits))).json()
    critical = (await _summary(_agent(circuits=circuits, executable=[]))).json()

    assert degraded["now"]["degradation_state"] == "degraded"
    assert critical["now"]["degradation_state"] == "critical"
    assert critical["now"]["pool_healthy"] == 0


async def test_an_empty_pool_is_not_called_critical_by_the_health_rule():
    body = (await _summary(_agent(pool=()))).json()
    assert body["now"]["degradation_state"] == "nominal"
    assert body["now"]["pool_size"] == 0


# ── attention ───────────────────────────────────────────────────────────────


async def test_circuit_breaker_items():
    circuits = {
        "a": {"state": "open", "last_state_change": time.time() - 500},
        "b": {"state": "half_open", "last_state_change": time.time() - 5},
        "c": {"state": "closed"},
    }

    body = (await _summary(_agent(pool=("a", "b", "c"), circuits=circuits))).json()

    items = {i["id"]: i for i in body["attention"]}
    assert set(items) == {"cb:a", "cb:b"}
    a, b = items["cb:a"], items["cb:b"]
    assert (a["kind"], a["severity"], a["state"]) == ("circuit_breaker_open", "critical", "persistent")
    assert (b["kind"], b["severity"], b["state"]) == ("circuit_breaker_half_open", "warning", "new")
    assert a["blast_radius"] == "endpoint:a" and a["owner"] == "circuit_breaker"
    assert a["suggested_actions"] == ["reset_cb", "mute"]
    assert a["confidence"] == 1.0 and a["baseline_delta"] == "N/A"
    assert a["age_sec"] >= 499


async def test_threat_ledger_items_for_ips_and_keys():
    now = time.time()
    ledger = _Ledger(
        ips={
            "1.1.1.1": [(2.0, now - 120), (1.5, now - 100)],  # 3.5 >= 3.0: blocked
            "2.2.2.2": [(1.2, now - 10)],  # 1.2: warning
            "3.3.3.3": [(0.5, now)],  # < 1.0: ignored
        },
        keys={"sk-abc": [(3.0, now - 5)]},
    )

    body = (await _summary(_agent(ledger=ledger))).json()

    items = {i["id"]: i for i in body["attention"]}
    assert set(items) == {"threat:ip:1.1.1.1", "threat:ip:2.2.2.2", "threat:key:sk-abc"}
    blocked, warn, key = items["threat:ip:1.1.1.1"], items["threat:ip:2.2.2.2"], items["threat:key:sk-abc"]
    assert (blocked["kind"], blocked["severity"]) == ("actor_blocked", "critical")
    assert blocked["confidence"] == 1.0 and blocked["baseline_delta"] == "+350% delta"
    assert (warn["kind"], warn["severity"], warn["state"]) == ("high_threat_score", "warning", "new")
    assert warn["confidence"] == 0.4 and warn["blast_radius"] == "ip:2.2.2.2"
    assert key["blast_radius"] == "key:sk-abc" and key["kind"] == "actor_blocked"
    assert blocked["owner"] == "threat_ledger"
    assert blocked["suggested_actions"] == ["mute_actor", "inspect_logs"]


async def test_an_empty_registry_item():
    body = (await _summary(_agent(pool=()))).json()

    (item,) = body["attention"]
    assert item["id"] == "registry:empty" and item["kind"] == "empty_registry"
    assert item["severity"] == "critical" and item["blast_radius"] == "gateway"
    assert item["suggested_actions"] == ["add_endpoint"] and item["state"] == "persistent"
    assert item["age_sec"] >= 99


async def test_budget_items():
    over = (await _summary(_agent(spent=75.0))).json()["attention"]
    soft = (await _summary(_agent(spent=45.0))).json()["attention"]
    under = (await _summary(_agent(spent=10.0))).json()["attention"]
    unlimited = (await _summary(_agent(daily=0.0, soft=0.0, spent=999.0))).json()["attention"]

    assert [(i["id"], i["kind"], i["severity"], i["baseline_delta"]) for i in over] == [
        ("budget:limit_exceeded", "budget_exhausted", "critical", "+50% delta")
    ]
    assert [(i["id"], i["kind"], i["severity"], i["baseline_delta"]) for i in soft] == [
        ("budget:soft_limit_exceeded", "budget_warning", "warning", "+12% delta")
    ]
    assert under == [] and unlimited == []
    assert over[0]["blast_radius"] == "all" and over[0]["suggested_actions"] == ["increase_limit"]


async def test_attention_is_sorted_critical_first_then_by_confidence():
    now = time.time()
    ledger = _Ledger(ips={"9.9.9.9": [(1.2, now)], "8.8.8.8": [(2.4, now)]})  # 0.4, 0.8
    circuits = {"a": {"state": "half_open", "last_state_change": now}}

    body = (await _summary(_agent(ledger=ledger, circuits=circuits, spent=60.0))).json()

    order = [(i["severity"], i["id"]) for i in body["attention"]]
    assert order[0] == ("critical", "budget:limit_exceeded")
    warnings = [i for s, i in order if s == "warning"]
    assert warnings == ["cb:a", "threat:ip:8.8.8.8", "threat:ip:9.9.9.9"]


# ── do next ─────────────────────────────────────────────────────────────────


async def test_each_kind_maps_to_its_task():
    now = time.time()
    ledger = _Ledger(ips={"1.1.1.1": [(5.0, now)]})
    circuits = {"a": {"state": "open", "last_state_change": now}}

    body = (await _summary(_agent(pool=(), ledger=ledger, circuits=circuits, spent=80.0))).json()

    tasks = {t["id"]: t for t in body["do_next"]}
    assert set(tasks) == {
        "task:reset_cb:a",
        "task:inspect_logs:ip:1.1.1.1",
        "task:add_endpoint",
        "task:increase_limit",
    }
    assert tasks["task:reset_cb:a"]["action"] == "reset_cb"
    assert tasks["task:reset_cb:a"]["target"] == "a"
    assert tasks["task:inspect_logs:ip:1.1.1.1"]["target"] == "ip:1.1.1.1"
    assert tasks["task:add_endpoint"]["target"] == ""
    assert "Reset circuit breaker for a" == tasks["task:reset_cb:a"]["title"]
    assert "view_analytics" not in {t["action"] for t in body["do_next"]}  # list is long enough


async def test_default_tasks_are_added_only_when_fewer_than_two():
    one = (await _summary(_agent(spent=80.0))).json()["do_next"]  # one task: budget

    assert [t["id"] for t in one] == [
        "task:increase_limit", "task:review_budget", "task:check_plugins",
    ]


# ── recent changes ──────────────────────────────────────────────────────────


async def test_recent_changes_come_from_the_audit_log():
    items = [
        {"ts": 111, "req_id": "r1", "provider": "openai", "model": "gpt-4o", "status": 200},
        {"ts": 222, "req_id": "r2", "provider": "openai", "model": "gpt-4o", "status": 403,
         "blocked": 1, "block_reason": "injection"},
    ]

    body = (await _summary(_agent(audit_items=items))).json()

    assert body["recent_changes"] == [
        {"timestamp": 111, "type": "request",
         "description": "Request r1 processed on openai/gpt-4o (HTTP 200)"},
        {"timestamp": 222, "type": "audit_block",
         "description": "Blocked request r2 on gpt-4o: injection"},
    ]


# ── a broken section does not take the dashboard down ───────────────────────


async def test_a_failing_audit_query_leaves_the_rest_intact():
    agent = _agent(spent=60.0)
    agent.store.query_audit = AsyncMock(side_effect=RuntimeError("db down"))

    resp = await _summary(agent)

    assert resp.status_code == 200
    body = resp.json()
    assert _kinds(body) == ["budget_exhausted"]
    assert [c["type"] for c in body["recent_changes"]] == ["system_boot"]


async def test_a_failing_circuit_manager_still_reports_the_other_sections():
    agent = _agent(spent=60.0)
    agent.circuit_manager.get_all_states = AsyncMock(side_effect=RuntimeError("redis down"))

    resp = await _summary(agent)

    assert resp.status_code == 200
    assert _kinds(resp.json()) == ["budget_exhausted"]


async def test_a_failing_threat_ledger_still_reports_the_other_sections():
    class Broken:
        threshold = 3.0

        @property
        def _ip_ledger(self):
            raise RuntimeError("ledger corrupt")

    resp = await _summary(_agent(ledger=Broken(), spent=60.0))

    assert resp.status_code == 200
    assert _kinds(resp.json()) == ["budget_exhausted"]


async def test_a_malformed_budget_config_only_loses_the_budget_section():
    agent = _agent(spent=60.0)
    agent.config["budget"] = {"daily_limit": "fifty"}

    resp = await _summary(agent)

    assert resp.status_code == 200
    assert _kinds(resp.json()) == []


# ── a broken section is visible, not just logged ────────────────────────────


async def test_section_errors_name_the_sections_that_failed():
    agent = _agent(spent=60.0)
    agent.config["budget"] = {"daily_limit": "fifty"}
    agent.store.query_audit = AsyncMock(side_effect=RuntimeError("db down"))

    body = (await _summary(agent)).json()

    assert sorted(body["section_errors"]) == ["budget", "recent_changes"]
    assert body["attention"] == []  # absent because it failed, not because all is well


async def test_a_clean_summary_has_no_section_errors_key():
    assert "section_errors" not in (await _summary(_agent())).json()


async def test_a_failing_pool_query_is_reported_too():
    agent = _agent()
    agent.store.get_pool = AsyncMock(side_effect=RuntimeError("store down"))

    body = (await _summary(agent)).json()

    assert "pool" in body["section_errors"]
    assert body["now"]["pool_size"] == 0


# ── the pieces on their own ─────────────────────────────────────────────────


def test_attention_item_defaults_state_from_age_and_floors_a_negative_age():
    from proxy import dashboard

    base = dict(id="x", kind="k", severity="warning", blast_radius="b", owner="o", suggested_actions=[])

    assert dashboard.attention_item(age_sec=61, **base)["state"] == "persistent"
    assert dashboard.attention_item(age_sec=60, **base)["state"] == "new"
    assert dashboard.attention_item(age_sec=-5, **base)["age_sec"] == 0
    assert dashboard.attention_item(age_sec=1, state="persistent", **base)["state"] == "persistent"


def test_degradation_state_precedence():
    from proxy import dashboard

    pool = [object()]
    assert dashboard.degradation_state(0, pool, 1) == "nominal"
    assert dashboard.degradation_state(1, pool, 1) == "degraded"
    assert dashboard.degradation_state(1, pool, 0) == "critical"
    assert dashboard.degradation_state(0, [], 0) == "nominal"


def test_every_task_kind_has_a_builder_and_unknown_kinds_are_skipped():
    from proxy import dashboard

    assert set(dashboard.TASK_FOR_KIND) == {
        "circuit_breaker_open", "circuit_breaker_half_open", "high_threat_score",
        "actor_blocked", "empty_registry", "budget_exhausted", "budget_warning",
    }
    attention = [dashboard.attention_item(
        id="u", kind="something_new", severity="warning", blast_radius="b",
        age_sec=1, owner="o", suggested_actions=[])]
    assert [t["id"] for t in dashboard.do_next(attention)] == [
        "task:review_budget", "task:check_plugins"
    ]


def test_default_tasks_are_copies_so_callers_cannot_alter_the_defaults():
    from proxy import dashboard

    first = dashboard.do_next([])
    first[0]["title"] = "changed"
    assert dashboard.do_next([])[0]["title"] == "Review daily budget usage"


def test_budget_attention_prefers_the_hard_cap_over_the_soft_limit():
    from proxy import dashboard

    (item,) = dashboard.budget_attention({"daily_limit": 10, "soft_limit": 5}, 12.0, 0)
    assert item["kind"] == "budget_exhausted"
    assert dashboard.budget_attention({"daily_limit": 10, "soft_limit": 5}, 5.0, 0)[0]["kind"] == "budget_warning"
    assert dashboard.budget_attention({}, 99.0, 0) == []
