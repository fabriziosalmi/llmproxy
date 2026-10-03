"""The pieces of GET /api/v1/dashboard/summary.

The route used to be one 230-line closure, cyclomatic complexity 52: it gathered
state, built five kinds of attention item, sorted them, mapped them to tasks and
read the audit log. Adding or changing one alert meant reading all of it. Each
concern is a small pure function here, testable without an app or an agent
(tests/test_dashboard_summary.py pins what the route returns); the route only
gathers state and calls them.

Everything takes plain values (dicts, the pool list, the threat ledger) rather
than the orchestrator, so a function here cannot reach into a subsystem the
caller did not hand it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

#: An attention item older than this is "persistent", otherwise "new".
PERSISTENT_AFTER_S = 60

_SEVERITY_ORDER = {"critical": 0, "warning": 1}


def attention_item(
    *,
    id: str,
    kind: str,
    severity: str,
    blast_radius: str,
    age_sec: int,
    owner: str,
    suggested_actions: list[str],
    confidence: float = 1.0,
    baseline_delta: str = "N/A",
    state: str | None = None,
) -> dict[str, Any]:
    """One TriageIssue. ``state`` defaults from the age."""
    return {
        "id": id,
        "kind": kind,
        "severity": severity,
        "confidence": confidence,
        "blast_radius": blast_radius,
        "age_sec": max(0, age_sec),
        "baseline_delta": baseline_delta,
        "owner": owner,
        "suggested_actions": suggested_actions,
        "state": state or ("persistent" if age_sec > PERSISTENT_AFTER_S else "new"),
    }


# ── now ─────────────────────────────────────────────────────────────────────


def degradation_state(circuits_open: int, pool: list[Any], healthy_count: int) -> str:
    """nominal, degraded (a breaker is open) or critical (nothing can serve)."""
    if pool and healthy_count == 0:
        return "critical"
    if circuits_open > 0:
        return "degraded"
    return "nominal"


# ── attention ───────────────────────────────────────────────────────────────


def circuit_attention(circuit_states: dict[str, dict], now: float) -> list[dict]:
    """A breaker that is open or half-open, one item each."""
    items = []
    for endpoint_id, info in circuit_states.items():
        state = info.get("state", "closed")
        if state not in ("open", "half_open"):
            continue
        age = int(now - info.get("last_state_change", now))
        items.append(
            attention_item(
                id=f"cb:{endpoint_id}",
                kind=f"circuit_breaker_{state}",
                severity="critical" if state == "open" else "warning",
                blast_radius=f"endpoint:{endpoint_id}",
                age_sec=age,
                owner="circuit_breaker",
                suggested_actions=["reset_cb", "mute"],
            )
        )
    return items


def _actor_items(
    actors: Iterable[tuple[Any, list]], label: str, threshold: float, now: float
) -> list[dict]:
    items = []
    for actor, entries in actors:
        score_sum = sum(score for score, _ in entries)
        if score_sum < 1.0:
            continue
        blocked = score_sum >= threshold
        last_seen = max(ts for _, ts in entries) if entries else now
        items.append(
            attention_item(
                id=f"threat:{label}:{actor}",
                kind="actor_blocked" if blocked else "high_threat_score",
                severity="critical" if blocked else "warning",
                confidence=round(min(1.0, score_sum / threshold), 2),
                blast_radius=f"{label}:{actor}",
                age_sec=int(now - last_seen),
                baseline_delta=f"+{int(score_sum * 100)}% delta",
                owner="threat_ledger",
                suggested_actions=["mute_actor", "inspect_logs"],
            )
        )
    return items


def threat_attention(ledger: Any, now: float) -> list[dict]:
    """Suspicious IPs and keys from the threat ledger (empty when there is none)."""
    if not ledger:
        return []
    return _actor_items(
        list(ledger._ip_ledger.items()), "ip", ledger.threshold, now
    ) + _actor_items(list(ledger._key_ledger.items()), "key", ledger.threshold, now)


def registry_attention(pool: list[Any], uptime: float) -> list[dict]:
    if pool:
        return []
    return [
        attention_item(
            id="registry:empty",
            kind="empty_registry",
            severity="critical",
            blast_radius="gateway",
            age_sec=int(uptime),
            owner="registry",
            suggested_actions=["add_endpoint"],
            state="persistent",
        )
    ]


def budget_attention(budget_cfg: dict, spent: float, uptime: float) -> list[dict]:
    """Hard cap exceeded, else soft limit exceeded, else nothing."""
    daily = float(budget_cfg.get("daily_limit", 0.0))
    soft = float(budget_cfg.get("soft_limit", 0.0))
    for limit, id_, kind, severity, exceeded in (
        (daily, "budget:limit_exceeded", "budget_exhausted", "critical", spent >= daily),
        (soft, "budget:soft_limit_exceeded", "budget_warning", "warning", spent >= soft),
    ):
        if limit > 0 and exceeded:
            return [
                attention_item(
                    id=id_,
                    kind=kind,
                    severity=severity,
                    blast_radius="all",
                    age_sec=int(uptime),
                    baseline_delta=f"+{int((spent - limit) / limit * 100)}% delta",
                    owner="budget",
                    suggested_actions=["increase_limit"],
                    state="persistent",
                )
            ]
    return []


def sort_attention(items: list[dict]) -> list[dict]:
    """Critical first, then the more confident first."""
    return sorted(
        items, key=lambda i: (_SEVERITY_ORDER.get(i["severity"], 2), -i["confidence"])
    )


# ── do next ─────────────────────────────────────────────────────────────────


def _task(id_: str, title: str, description: str, action: str, target: str = "") -> dict:
    return {
        "id": id_,
        "title": title,
        "description": description,
        "action": action,
        "target": target,
    }


def _reset_cb(item: dict) -> dict:
    ep = item["blast_radius"].replace("endpoint:", "")
    return _task(
        f"task:reset_cb:{ep}",
        f"Reset circuit breaker for {ep}",
        f"Endpoint {ep} is offline or degraded. Reset it to CLOSED once the upstream is available.",
        "reset_cb",
        ep,
    )


def _inspect_logs(item: dict) -> dict:
    target = item["blast_radius"]
    return _task(
        f"task:inspect_logs:{target}",
        f"Inspect logs for {target}",
        f"Actor {target} has elevated threat score. Inspect live logs for prompt injection attempts.",
        "inspect_logs",
        target,
    )


def _add_endpoint(_item: dict) -> dict:
    return _task(
        "task:add_endpoint",
        "Add a new endpoint",
        "Gateway has no active endpoints configured. Register an OpenAI, Ollama, or Anthropic provider.",
        "add_endpoint",
    )


def _increase_limit(_item: dict) -> dict:
    return _task(
        "task:increase_limit",
        "Increase daily budget limit",
        "Today's spend is near or exceeds the daily cap. Increase budget.daily_limit in config.yaml.",
        "increase_limit",
    )


#: attention kind -> the task that answers it.
TASK_FOR_KIND: dict[str, Callable[[dict], dict]] = {
    "circuit_breaker_open": _reset_cb,
    "circuit_breaker_half_open": _reset_cb,
    "high_threat_score": _inspect_logs,
    "actor_blocked": _inspect_logs,
    "empty_registry": _add_endpoint,
    "budget_exhausted": _increase_limit,
    "budget_warning": _increase_limit,
}

_DEFAULT_TASKS = (
    _task(
        "task:review_budget",
        "Review daily budget usage",
        "System spend is within nominal limits. Review cost efficiency under the Analytics tab.",
        "view_analytics",
    ),
    _task(
        "task:check_plugins",
        "Inspect active plugins",
        "Ensure guards (PII, injection) are active and running under their target hooks.",
        "view_plugins",
    ),
)


def do_next(attention: list[dict]) -> list[dict]:
    """One task per attention item that has one; defaults pad a short list."""
    tasks = [
        TASK_FOR_KIND[item["kind"]](item)
        for item in attention
        if item["kind"] in TASK_FOR_KIND
    ]
    if len(tasks) < 2:
        tasks.extend(dict(t) for t in _DEFAULT_TASKS)
    return tasks


# ── recent changes ──────────────────────────────────────────────────────────


def recent_changes(audit_items: list[dict], now: float) -> list[dict]:
    """The latest audit rows as change entries, or a boot placeholder."""
    changes = []
    for item in audit_items:
        description = (
            f"Request {item['req_id']} processed on {item['provider']}/{item['model']} "
            f"(HTTP {item['status']})"
        )
        if item.get("blocked"):
            description = (
                f"Blocked request {item['req_id']} on {item['model']}: {item['block_reason']}"
            )
        changes.append(
            {
                "timestamp": item["ts"],
                "type": "audit_block" if item.get("blocked") else "request",
                "description": description,
            }
        )
    return changes or [
        {
            "timestamp": int(now),
            "type": "system_boot",
            "description": "System operational, waiting for incoming requests.",
        }
    ]
