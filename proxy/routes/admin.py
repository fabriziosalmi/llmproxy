"""Admin routes: proxy toggle, status, version, service-info, features, priority, panic."""

import os
import time
import logging
from typing import Any

from fastapi import APIRouter, Request, HTTPException

logger = logging.getLogger("llmproxy.routes.admin")


# ── Spend forecasting math (M.2) ────────────────────────────────────────
# Pure helper so tests can drive elapsed_hours / spent / daily_limit
# directly without monkey-patching wall-clock time.

# Below this many hours elapsed since midnight, a burn-rate forecast is
# noise (one cheap call right after midnight extrapolates to wild
# numbers). Return null fields rather than mislead operators.
_FORECAST_MIN_HOURS = 5.0 / 60.0  # 5 minutes


def _compute_forecast(
    *, spent: float, daily_limit: float, elapsed_hours: float
) -> dict:
    """Return today's spend forecast block.

    All money fields are USD. None values mean "not enough data yet" or
    "not applicable" — the consumer should render those as `—`.
    """
    block: dict[str, Any] = {
        "current_spend_usd": round(spent, 6),
        "daily_limit_usd": round(daily_limit, 4) if daily_limit > 0 else None,
        "elapsed_hours": round(elapsed_hours, 3),
        "burn_rate_usd_per_hour": None,
        "projected_daily_total_usd": None,
        "headroom_usd": None,
        "time_to_limit_hours": None,
    }

    if elapsed_hours < _FORECAST_MIN_HOURS:
        return block

    burn_rate = spent / elapsed_hours
    block["burn_rate_usd_per_hour"] = round(burn_rate, 6)
    block["projected_daily_total_usd"] = round(burn_rate * 24, 4)

    if daily_limit > 0:
        headroom = daily_limit - spent
        block["headroom_usd"] = round(headroom, 4)
        if headroom <= 0:
            # Already over — surface a hard zero so the UI can render
            # "limit exceeded" without dividing by zero.
            block["time_to_limit_hours"] = 0.0
        elif burn_rate > 0:
            block["time_to_limit_hours"] = round(headroom / burn_rate, 3)
        # burn_rate == 0 with headroom > 0: leave time_to_limit None — the
        # forecast is "indefinite at zero rate".

    return block


def create_router(agent) -> APIRouter:
    router = APIRouter()

    def _parse_int_param(
        raw: str | None, *, default: int, minimum: int, maximum: int
    ) -> int:
        try:
            val = int(raw) if raw is not None else default
        except (TypeError, ValueError):
            val = default
        return max(minimum, min(maximum, val))

    def _check_admin_auth(request: Request):
        """Enforce API key auth on mutating admin endpoints when auth is enabled.

        Read-only endpoints (status, metrics, version) remain open so dashboards
        can poll without credentials. Mutating / destructive endpoints (panic,
        toggle, hot-swap) require the same API key used for chat requests.
        """
        if not agent.config.get("server", {}).get("auth", {}).get("enabled", False):
            return  # Auth disabled — development mode, allow all
        auth_header = request.headers.get("Authorization", "")
        token = auth_header.replace("Bearer ", "").strip()
        
        if hasattr(agent, "jwt_authenticator") and agent.jwt_authenticator.enabled:
            if not agent.jwt_authenticator.verify_token(token):
                raise HTTPException(status_code=401, detail="Admin: Unauthorized (Invalid JWT)")
            return
            
        if not agent._verify_api_key(token):
            raise HTTPException(status_code=401, detail="Admin: Unauthorized")

    @router.post("/api/v1/proxy/toggle")
    async def toggle_proxy_service(request: Request):
        _check_admin_auth(request)
        data = await request.json()
        agent.proxy_enabled = data.get("enabled", not agent.proxy_enabled)
        await agent.store.set_state("proxy_enabled", agent.proxy_enabled)
        status = "ACTIVE" if agent.proxy_enabled else "STOPPED"
        await agent._add_log(f"SYSTEM: Proxy service {status}")
        return {"status": status, "enabled": agent.proxy_enabled}

    @router.get("/api/v1/proxy/status")
    async def get_proxy_status():
        return {"enabled": agent.proxy_enabled, "priority_mode": agent.priority_mode}

    @router.get("/api/v1/version")
    async def get_version():
        version_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "VERSION"
        )
        version_path = os.path.normpath(version_path)
        if os.path.exists(version_path):
            with open(version_path, "r") as f:
                return {"version": f.read().strip()}
        return {"version": "0.1.0-alpha"}

    @router.get("/api/v1/service-info")
    async def get_service_info(request: Request):
        port = agent.config.get("server", {}).get("port", 8090)
        return {
            "host": request.client.host if request.client else "0.0.0.0",  # nosec B104
            "port": port,
            "url": f"http://{request.client.host if request.client else 'localhost'}:{port}/v1",
        }

    @router.get("/api/v1/features")
    async def get_features():
        return agent.features

    @router.get("/api/v1/network/info")
    async def get_network_info():
        return {
            "host": agent.config.get("server", {}).get("host", "0.0.0.0"),  # nosec B104
            "port": agent.config.get("server", {}).get("port", 8090),
            "tailscale_active": agent.config.get("server", {}).get("host")
            not in ("0.0.0.0", "127.0.0.1"),  # nosec B104
            "version": getattr(agent, "_version", "0.0.0"),
        }

    @router.post("/api/v1/features/toggle")
    async def toggle_feature(request: Request):
        _check_admin_auth(request)
        data = await request.json()
        name = data.get("name")
        if name in agent.features:
            agent.features[name] = data.get("enabled", not agent.features[name])
            await agent.store.set_state(f"feature_{name}", agent.features[name])
            await agent._add_log(
                f"SHIELD: Feature '{name}' {'ENABLED' if agent.features[name] else 'DISABLED'}"
            )
            agent.security.config[name] = {"enabled": agent.features[name]}
            return {"name": name, "enabled": agent.features[name]}
        raise HTTPException(status_code=400, detail="Unknown feature")

    @router.post("/api/v1/proxy/priority/toggle")
    async def toggle_priority_mode(request: Request):
        _check_admin_auth(request)
        data = await request.json()
        agent.priority_mode = data.get("enabled", False)
        await agent.store.set_state("priority_mode", agent.priority_mode)
        await agent._add_log(
            f"SYSTEM: Priority Steering {'ENABLED' if agent.priority_mode else 'DISABLED'}"
        )
        return {"enabled": agent.priority_mode}

    # ── Spend forecasting (M.2) ──

    def _forecast_block() -> dict:
        """Project today's spend forward at the current burn rate.

        Embedded in /analytics/spend and exposed standalone at /analytics/forecast.
        Time-to-limit is the most actionable single number for an operator —
        it's what they'd compute by hand otherwise.
        """
        import datetime as _dt

        daily_limit = float(agent.config.get("budget", {}).get("daily_limit", 0.0))
        spent = float(getattr(agent, "total_cost_today", 0.0))
        now = _dt.datetime.now()
        midnight = _dt.datetime.combine(now.date(), _dt.time.min)
        elapsed_hours = max((now - midnight).total_seconds() / 3600.0, 0.0)
        return _compute_forecast(
            spent=spent, daily_limit=daily_limit, elapsed_hours=elapsed_hours
        )

    @router.get("/api/v1/analytics/forecast")
    async def get_spend_forecast(request: Request):
        """Today's forecast: burn rate, projected total, headroom, time-to-limit.

        Numbers are derived from agent.total_cost_today and the wall-clock
        elapsed since local midnight. The same block is embedded in
        /analytics/spend so existing dashboards pick it up for free.
        """
        _check_admin_auth(request)
        return _forecast_block()

    # ── Routing config (cost weight + strategy) ──

    def _routing_block() -> dict:
        cw = getattr(agent, "routing_cost_weight", None)
        if cw is None:
            cw = float(agent.config.get("routing", {}).get("cost_weight", 0.3))
        if agent.priority_mode:
            strategy = "priority"
        elif cw <= 0.0:
            strategy = "performance"
        else:
            strategy = "smart_weighted"
        return {
            "cost_weight": round(float(cw), 4),
            "priority_mode": bool(agent.priority_mode),
            "strategy": strategy,
        }

    @router.get("/api/v1/routing/config")
    async def get_routing_config(request: Request):
        """Read the live routing configuration: cost_weight + active strategy."""
        _check_admin_auth(request)
        return _routing_block()

    # ── Rate-limit presets (N.6) ──

    @router.get("/api/v1/rate-limit/config")
    async def get_rate_limit_config(request: Request):
        """Live rate-limit config: enabled flag + active preset (if any) +
        the rpm/burst the limiter is currently serving."""
        _check_admin_auth(request)
        from core.rate_limiter import RateLimitMiddleware, RATE_LIMIT_PRESETS

        block: dict = {"presets": RATE_LIMIT_PRESETS}
        if RateLimitMiddleware.instance is not None:
            block.update(RateLimitMiddleware.instance.current_config())
        else:
            # Fallback when the middleware was never instantiated (tests, no-app).
            cfg = agent.config.get("rate_limiting", {})
            block.update(
                {
                    "enabled": cfg.get("enabled", False),
                    "preset": None,
                    "requests_per_minute": cfg.get("requests_per_minute", 60),
                    "burst": cfg.get("burst", 10),
                }
            )
        return block

    @router.post("/api/v1/rate-limit/preset")
    async def set_rate_limit_preset(request: Request):
        """Apply a named rate-limit preset (strict / normal / relaxed) at
        runtime. Existing per-IP buckets are flushed so the new caps take
        effect immediately. Persists to the store, restart-safe."""
        _check_admin_auth(request)
        from core.rate_limiter import RateLimitMiddleware, RATE_LIMIT_PRESETS

        data = await request.json()
        name = (data.get("preset") or "").lower().strip()
        if name not in RATE_LIMIT_PRESETS:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown preset '{name}'. Valid: {list(RATE_LIMIT_PRESETS)}",
            )
        if RateLimitMiddleware.instance is None:
            raise HTTPException(
                status_code=503,
                detail="Rate-limit middleware not initialised — preset cannot be applied",
            )
        applied = await RateLimitMiddleware.instance.apply_preset(name)
        await agent.store.set_state("rate_limit:preset", name)
        await agent._add_log(
            f"SYSTEM: Rate-limit preset → {name} "
            f"({applied['requests_per_minute']}/min, burst={applied['burst']})",
            level="SYSTEM",
        )
        return {"preset": name, **applied}

    @router.post("/api/v1/routing/cost-weight")
    async def set_routing_cost_weight(request: Request):
        """Update the cost-bias weight at runtime.

        Accepts {"cost_weight": float in [0.0, 1.0]}. 0.0 ignores cost (pure
        performance), 1.0 fully biases toward cheaper models. Persisted to
        the store so restarts survive; smart_router picks up the change on
        the very next request.
        """
        _check_admin_auth(request)
        data = await request.json()
        try:
            new_weight = float(data.get("cost_weight"))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="cost_weight must be a number")
        if not 0.0 <= new_weight <= 1.0:
            raise HTTPException(
                status_code=400, detail="cost_weight must be in [0.0, 1.0]"
            )
        agent.routing_cost_weight = new_weight
        await agent.store.set_state("routing:cost_weight", new_weight)
        await agent._add_log(
            f"SYSTEM: Routing cost_weight set to {new_weight:.2f}",
            level="SYSTEM",
        )
        return _routing_block()

    @router.get("/api/v1/guards/status")
    async def get_guards_status():
        """Consolidated security subsystem status."""
        from core.firewall_asgi import ByteLevelFirewallMiddleware

        return {
            "features": agent.features,
            "circuit_breakers": await agent.circuit_manager.get_all_states(),
            "firewall": {
                "enabled": getattr(agent, "firewall_enabled", True),
                "disabled_reason": getattr(agent, "firewall_disabled_reason", None),
                "total_scanned": ByteLevelFirewallMiddleware.total_scanned,
                "total_blocked": ByteLevelFirewallMiddleware.total_blocked,
                "block_by_signature": ByteLevelFirewallMiddleware.block_by_signature,
                "signatures_count": len(
                    ByteLevelFirewallMiddleware._FALLBACK_SIGNATURES
                ),
            },
            "rate_limiter": {
                "enabled": agent.config.get("rate_limiting", {}).get("enabled", False),
                "requests_per_minute": agent.config.get("rate_limiting", {}).get(
                    "requests_per_minute", 60
                ),
            },
            "budget": {
                "total_cost_today": agent.total_cost_today,
                "daily_limit": agent.config.get("budget", {}).get("daily_limit", 0),
                "soft_limit": agent.config.get("budget", {}).get("soft_limit", 0),
                "budget_date": agent._budget_date,
            },
        }

    @router.get("/api/v1/webhooks")
    async def get_webhooks():
        """List configured webhook endpoints."""
        from core.webhooks import EventType

        return {
            "enabled": agent.webhooks.enabled,
            "endpoints": [
                {
                    "name": ep.name,
                    "target": ep.target.value,
                    "events": ep.events,
                    "url_masked": ep.url[:20] + "..." if len(ep.url) > 20 else ep.url,
                }
                for ep in agent.webhooks.endpoints
            ],
            "event_types": [e.value for e in EventType],
        }

    @router.get("/api/v1/export/status")
    async def get_export_status():
        """Export subsystem status."""
        if not agent.exporter:
            return {"enabled": False}
        import os

        export_dir = str(agent.exporter.output_dir)
        files = []
        if os.path.isdir(export_dir):
            for f in sorted(os.listdir(export_dir), reverse=True)[:10]:
                fp = os.path.join(export_dir, f)
                if os.path.isfile(fp):
                    files.append({"name": f, "size_bytes": os.path.getsize(fp)})
        return {
            "enabled": True,
            "output_dir": export_dir,
            "scrub_pii": agent.exporter.scrub,
            "compress": agent.exporter.compress_on_rotate,
            "current_date": str(agent.exporter._current_date)
            if agent.exporter._current_date
            else None,
            "files": files,
        }

    @router.get("/api/v1/rbac/roles")
    async def get_rbac_roles():
        """RBAC role matrix."""
        return {role: sorted(perms) for role, perms in agent.rbac.permissions.items()}

    @router.get("/api/v1/metrics/latency")
    async def get_latency_metrics():
        """Per-ring and per-plugin latency percentiles (P50/P95/P99)."""
        plugin_stats = agent.plugin_manager.get_plugin_stats()
        ring_latency = agent.plugin_manager.get_ring_latency()

        # Also extract TTFT from Prometheus if available
        ttft_samples = []
        # Collect from ring traces
        for trace in agent.plugin_manager._ring_traces:
            if "ttft_ms" in trace:
                ttft_samples.append(trace["ttft_ms"])

        ttft_percentiles = (
            agent.plugin_manager._percentiles(ttft_samples)
            if ttft_samples
            else {"p50": 0, "p95": 0, "p99": 0}
        )

        return {
            "rings": ring_latency,
            "plugins": {
                name: {
                    "avg_ms": s.get("avg_latency_ms", 0),
                    "percentiles": s.get("latency_percentiles", {}),
                    "invocations": s.get("invocations", 0),
                }
                for name, s in plugin_stats.items()
            },
            "ttft": {
                "samples": len(ttft_samples),
                **ttft_percentiles,
            },
        }

    @router.get("/api/v1/metrics/ring-timeline")
    async def get_ring_timeline():
        """Recent request traces with per-ring execution breakdown."""
        limit = 20
        traces = agent.plugin_manager.get_ring_traces(limit)
        return {"traces": traces, "count": len(traces)}

    @router.get("/api/v1/cache/stats")
    async def get_cache_stats():
        """Cache subsystem status (L1 negative + L2 positive)."""
        l2_stats = await agent.cache_backend.stats()
        l1_stats = agent.negative_cache.stats()
        return {
            "negative_cache": l1_stats,
            "positive_cache": l2_stats,
        }

    @router.post("/api/v1/admin/reload")
    async def reload_config(request: Request):
        """Hot-reload config.yaml without restart."""
        _check_admin_auth(request)
        old_cfg = agent.config
        old_hash = agent._config_hash
        old_webhooks = getattr(agent, "webhooks", None)
        try:
            agent.config = agent._load_config()
            new_hash = agent._compute_config_hash_sync()
            agent._config_hash = new_hash
            # Reinitialize config-dependent subsystems
            from core.webhooks import WebhookDispatcher

            new_webhooks = WebhookDispatcher(agent.config)
            agent.webhooks = new_webhooks
            if old_webhooks and old_webhooks is not new_webhooks:
                try:
                    await old_webhooks.close()
                except Exception:
                    logger.warning(
                        "Previous webhook dispatcher close failed during reload",
                        exc_info=True,
                    )
            from core.security import SecurityShield

            agent.security = SecurityShield(
                agent.config, assistant=agent.security.assistant
            )
            await agent._add_log("Config reloaded via admin API", level="SYSTEM")
            return {"status": "reloaded", "changed": old_hash != new_hash}
        except Exception as e:
            agent.config = old_cfg
            agent._config_hash = old_hash
            logger.error(f"Config reload failed: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="Config reload failed")

    @router.post("/api/v1/panic")
    async def emergency_panic(request: Request):
        _check_admin_auth(request)
        from core.webhooks import EventType

        agent.proxy_enabled = False
        await agent.store.set_state("proxy_enabled", False)
        await agent._add_log(
            "EMERGENCY: Panic Kill-Switch activated. ALL TRAFFIC DROPPED.",
            level="CRITICAL",
        )
        await agent.webhooks.dispatch(
            EventType.PANIC_ACTIVATED,
            {"action": "kill_switch", "timestamp": time.strftime("%H:%M:%S")},
        )
        return {"status": "HALTED"}

    # ── Spend Analytics (R2.3) ──

    @router.get("/api/v1/analytics/spend")
    async def analytics_spend(request: Request):
        """Spend breakdown by model, provider, key, or date."""
        _check_admin_auth(request)
        params = request.query_params
        limit = _parse_int_param(
            params.get("limit"), default=50, minimum=1, maximum=1000
        )
        result = await agent.store.query_spend(
            date_from=params.get("from", ""),
            date_to=params.get("to", ""),
            group_by=params.get("group_by", "model"),
            limit=limit,
        )
        total = await agent.store.get_spend_total(
            date_from=params.get("from", ""),
            date_to=params.get("to", ""),
        )
        return {
            "total": total,
            "breakdown": result,
            "routing": _routing_block(),
            "forecast": _forecast_block(),
        }

    @router.get("/api/v1/analytics/spend/topmodels")
    async def analytics_top_models(request: Request):
        """Top models by spend."""
        _check_admin_auth(request)
        limit = _parse_int_param(
            request.query_params.get("limit"), default=10, minimum=1, maximum=200
        )
        result = await agent.store.query_spend(
            group_by="model",
            limit=limit,
        )
        return result

    @router.get("/api/v1/analytics/cost-efficiency")
    async def analytics_cost_efficiency(request: Request):
        """Cost efficiency: avg cost/request + savings vs premium baseline."""
        _check_admin_auth(request)
        params = request.query_params
        by_model = await agent.store.query_spend(
            date_from=params.get("from", ""),
            date_to=params.get("to", ""),
            group_by="model",
            limit=100,
        )
        total = await agent.store.get_spend_total(
            date_from=params.get("from", ""),
            date_to=params.get("to", ""),
        )
        # Compute per-model efficiency
        efficiency = []
        for row in by_model:
            reqs = row.get("requests", 0) or 1
            cost = row.get("total_cost_usd", 0.0)
            total_tokens = (row.get("total_prompt_tokens", 0) or 0) + (
                row.get("total_completion_tokens", 0) or 0
            )
            avg_cost = cost / max(reqs, 1)
            efficiency.append(
                {
                    "model": row.get("model", "unknown"),
                    "requests": reqs,
                    "total_cost_usd": round(cost, 4),
                    "avg_cost_per_request_usd": round(avg_cost, 6),
                    "avg_tokens_per_request": round(total_tokens / max(reqs, 1)),
                }
            )
        efficiency.sort(key=lambda x: x["avg_cost_per_request_usd"])

        # Savings vs a premium-tier baseline. Honest scope: this measures
        # model-mix economics, not the routing slider in isolation.
        # See core.pricing.estimate_baseline_savings for the contract.
        from core.pricing import estimate_baseline_savings

        savings = estimate_baseline_savings(by_model)

        return {
            "period_total_usd": total,
            "models": efficiency,
            "cheapest_model": efficiency[0]["model"] if efficiency else None,
            "most_expensive_model": efficiency[-1]["model"] if efficiency else None,
            "routing": _routing_block(),
            "savings": savings,
        }

    # ── Audit Log (R2.10) ──

    @router.get("/api/v1/audit/verify")
    async def verify_audit_chain():
        """Verify the integrity of the audit log hash chain.

        Walks every entry and recomputes SHA256 hashes. If any entry was
        modified, deleted, or inserted out of order, the chain breaks.
        Returns {"valid": true/false, "total": N, "verified": N, "broken_at": id|null}.
        """
        return await agent.store.verify_audit_chain()

    @router.get("/api/v1/metrics/hourly-buckets")
    async def get_hourly_buckets(request: Request):
        """Q.3 — Hourly ring buffer of KPI counters/gauges, fed by the
        background metrics_history_loop in proxy/background.py.

        Returns:
            {
              "hours": 24,
              "interval_s": 3600,
              "series": {
                "requests": [12, 8, 5, ...],   // 24 entries, oldest first
                "blocked":  [0, 1, 0, ...],
                "errors":   [...],
                "auth_failures": [...],
                "cost_usd": [...]              // gauge, not delta
              }
            }

        Empty `series` (no buckets yet) is a valid response — proxies
        that just started haven't accumulated history; the UI handles
        "render skeleton until at least 2 points".
        """
        _check_admin_auth(request)
        history = getattr(agent, "metrics_history", None)
        interval_s = int(
            agent.config.get("metrics", {}).get("history_interval_s", 3600)
        )
        slots = getattr(history, "slots", 24) if history is not None else 24
        return {
            "hours": slots,
            "interval_s": interval_s,
            "series": history.snapshot() if history is not None else {},
        }

    @router.get("/api/v1/openapi.json")
    async def get_openapi_schema(request: Request):
        """Auth-gated OpenAPI schema mirror.

        Q.2: FastAPI's default `/openapi.json` is disabled at app construction
        when auth is enabled (proxy/app_factory.py — full route map is recon
        for an unauthenticated attacker). This mirror sits behind the same
        admin auth as the rest of /api/v1/*, so a logged-in operator can still
        feed the schema into Swagger UI or generate clients without restarting
        the proxy in development mode.
        """
        _check_admin_auth(request)
        try:
            schema = request.app.openapi()
        except Exception as e:  # noqa: BLE001
            logger.error(f"OpenAPI schema build failed: {e}", exc_info=True)
            raise HTTPException(
                status_code=500, detail="OpenAPI schema build failed"
            ) from e
        return schema

    @router.get("/api/v1/config/yaml")
    async def get_config_yaml(request: Request):
        """Return the active config rendered as YAML, with secrets redacted.

        O.5: gives operators a Terraform-vibe "this is what the proxy is
        actually running with" view in Settings — including auto-discovered
        endpoints, env-merged values, and runtime mutations (preset, cost
        weight, …) that the on-disk config.yaml doesn't yet reflect. Secret
        fields (api keys, tokens, passwords) are redacted via core.export
        scrub_dict before serialisation, so a leaked screenshot doesn't
        leak credentials.
        """
        _check_admin_auth(request)
        import yaml as _yaml
        from core.export import scrub_dict

        try:
            redacted = scrub_dict(agent.config or {})
            text = _yaml.safe_dump(redacted, default_flow_style=False, sort_keys=False)
        except Exception as e:  # noqa: BLE001 — surface, don't crash the route
            logger.error(f"YAML serialisation failed: {e}", exc_info=True)
            raise HTTPException(
                status_code=500, detail="YAML serialisation failed"
            ) from e
        return {"yaml": text}

    @router.get("/api/v1/config/warnings")
    async def get_config_warnings(request: Request):
        """Surface startup-validation warnings to the admin UI.

        Returns the same list `run_startup_checks` logged at boot so the
        Settings → Config Warnings widget can show actionable copy
        ("OPENAI_API_KEY missing", "no endpoints configured", etc.) without
        requiring operators to grep journalctl.
        """
        _check_admin_auth(request)
        from core.startup_checks import get_startup_warnings

        return {"warnings": get_startup_warnings()}

    @router.get("/api/v1/audit")
    async def query_audit_log(request: Request):
        """Query persistent audit log with filters."""
        _check_admin_auth(request)
        params = request.query_params
        status = _parse_int_param(
            params.get("status"), default=0, minimum=0, maximum=599
        )
        blocked = _parse_int_param(
            params.get("blocked"), default=-1, minimum=-1, maximum=1
        )
        limit = _parse_int_param(
            params.get("limit"), default=100, minimum=1, maximum=1000
        )
        offset = _parse_int_param(
            params.get("offset"), default=0, minimum=0, maximum=1_000_000
        )
        return await agent.store.query_audit(
            date_from=params.get("from", ""),
            date_to=params.get("to", ""),
            model=params.get("model", ""),
            key_prefix=params.get("key_prefix", ""),
            status=status,
            blocked=blocked,
            limit=limit,
            offset=offset,
        )

    # ── Operations: Reset & Clear ──

    @router.post("/api/v1/firewall/reset")
    async def reset_firewall_counters(request: Request):
        """Reset all firewall WAF counters to zero."""
        _check_admin_auth(request)
        from core.firewall_asgi import ByteLevelFirewallMiddleware

        ByteLevelFirewallMiddleware.total_scanned = 0
        ByteLevelFirewallMiddleware.total_blocked = 0
        ByteLevelFirewallMiddleware.block_by_signature.clear()
        ByteLevelFirewallMiddleware.block_by_encoding.clear()
        ByteLevelFirewallMiddleware.total_scan_time_ms = 0.0
        ByteLevelFirewallMiddleware.max_scan_time_ms = 0.0
        return {"status": "reset", "message": "Firewall counters cleared"}

    @router.post("/api/v1/cache/clear")
    async def clear_caches(request: Request):
        """Clear L1 (negative) and/or L2 (positive) caches."""
        _check_admin_auth(request)
        result = {}
        # L1 negative cache
        if hasattr(agent, "negative_cache") and agent.negative_cache:
            agent.negative_cache.clear()
            result["negative_cache"] = "cleared"
        # L2 positive cache
        if hasattr(agent, "cache_backend") and agent.cache_backend:
            try:
                evicted = await agent.cache_backend.evict_expired()
                result["positive_cache"] = f"evicted {evicted} entries"
            except Exception as e:
                logger.error(
                    f"Cache eviction failed during clear_caches: {e}", exc_info=True
                )
                raise HTTPException(status_code=502, detail="Cache eviction failed")
        return {"status": "cleared", **result}

    @router.post("/api/v1/security/reset")
    async def reset_security_state(request: Request):
        """Reset SecurityShield session memory and threat ledger."""
        _check_admin_auth(request)
        result: dict[str, Any] = {}
        if hasattr(agent, "security"):
            sessions = len(agent.security.session_memory)
            agent.security.session_memory.clear()
            result["sessions_cleared"] = sessions
            if agent.security.threat_ledger:
                agent.security.threat_ledger._by_ip.clear()
                agent.security.threat_ledger._by_key.clear()
                result["threat_ledger"] = "cleared"
        return {"status": "reset", **result}

    @router.post("/api/v1/circuit-breaker/{endpoint_id}/reset")
    async def reset_circuit_breaker(endpoint_id: str, request: Request):
        """Manually reset a circuit breaker to CLOSED state."""
        _check_admin_auth(request)
        cb = await agent.circuit_manager.get_breaker(endpoint_id)
        async with cb._lock:
            from core.circuit_breaker import CircuitState

            cb.state = CircuitState.CLOSED
            cb.failure_count = 0
            cb._half_open_probe_active = False
        return {"status": "reset", "endpoint": endpoint_id, "state": "CLOSED"}

    @router.post("/api/v1/webhooks/test")
    async def test_webhook(request: Request):
        """Send a test payload to all configured webhook endpoints."""
        _check_admin_auth(request)
        from core.webhooks import EventType

        try:
            await agent.webhooks.dispatch(
                EventType.INJECTION_BLOCKED,
                {"message": "Test webhook from LLMProxy UI", "test": True},
            )
            return {
                "status": "sent",
                "message": "Test payload dispatched to all endpoints",
            }
        except Exception as e:
            logger.error(f"Webhook test dispatch failed: {e}", exc_info=True)
            raise HTTPException(status_code=502, detail="Webhook test dispatch failed")

    @router.get("/api/v1/dashboard/summary")
    async def get_dashboard_summary(request: Request):
        _check_admin_auth(request)
        try:
            # 1. NOW: Health, throughput, degradation state, auth mode
            uptime = time.time() - getattr(agent, "_start_time", time.time())
            
            pool = []
            healthy_count = 0
            circuits_open = 0
            try:
                pool = await agent.store.get_pool()
                for e in pool:
                    breaker = await agent.circuit_manager.get_breaker(e.id)
                    if await breaker.can_execute():
                        healthy_count += 1
                
                circuit_states = await agent.circuit_manager.get_all_states()
                circuits_open = sum(
                    1 for s in circuit_states.values() if s.get("state") == "open"
                )
            except Exception as e:
                logger.warning(f"Error querying pool status for summary: {e}")

            auth_enabled = agent.config.get("server", {}).get("auth", {}).get("enabled", False)
            
            # Coalesce overall health and degradation state
            degradation_state = "nominal"
            if circuits_open > 0:
                degradation_state = "degraded"
            if pool and healthy_count == 0:
                degradation_state = "critical"

            # Compute throughput using REQUEST_COUNT counter
            from core import metrics
            from core.metrics_history import sum_prometheus_counter
            total_requests = int(sum_prometheus_counter(metrics.REQUEST_COUNT))

            now_data = {
                "health": degradation_state,
                "uptime_seconds": round(uptime),
                "pool_size": len(pool),
                "pool_healthy": healthy_count,
                "auth_mode": "enabled" if auth_enabled else "disabled",
                "degradation_state": degradation_state,
                "throughput_today": total_requests,
            }

            # 2. ATTENTION: prioritized anomalies (TriageIssue list)
            attention = []
            
            # 2a. Circuit Breakers open/half_open
            try:
                circuit_states = await agent.circuit_manager.get_all_states()
                for ep_id, state_info in circuit_states.items():
                    st = state_info.get("state", "closed")
                    if st in ("open", "half_open"):
                        last_change = state_info.get("last_state_change", time.time())
                        age = int(time.time() - last_change)
                        attention.append({
                            "id": f"cb:{ep_id}",
                            "kind": f"circuit_breaker_{st}",
                            "severity": "critical" if st == "open" else "warning",
                            "confidence": 1.0,
                            "blast_radius": f"endpoint:{ep_id}",
                            "age_sec": max(0, age),
                            "baseline_delta": "N/A",
                            "owner": "circuit_breaker",
                            "suggested_actions": ["reset_cb", "mute"],
                            "state": "persistent" if age > 60 else "new"
                        })
            except Exception as e:
                logger.warning(f"Error scanning circuit states for summary: {e}")

            # 2b. Threat Ledger (suspicious IPs / keys)
            try:
                if getattr(agent.security, "threat_ledger", None):
                    ledger = agent.security.threat_ledger
                    # IPs
                    for ip, entries in list(ledger._ip_ledger.items()):
                        score_sum = sum(s for s, _ in entries)
                        if score_sum >= 1.0:
                            is_blocked = score_sum >= ledger.threshold
                            severity = "critical" if is_blocked else "warning"
                            kind = "actor_blocked" if is_blocked else "high_threat_score"
                            last_ts = max(ts for _, ts in entries) if entries else time.time()
                            age = int(time.time() - last_ts)
                            attention.append({
                                "id": f"threat:ip:{ip}",
                                "kind": kind,
                                "severity": severity,
                                "confidence": round(min(1.0, score_sum / ledger.threshold), 2),
                                "blast_radius": f"ip:{ip}",
                                "age_sec": max(0, age),
                                "baseline_delta": f"+{int(score_sum * 100)}% delta",
                                "owner": "threat_ledger",
                                "suggested_actions": ["mute_actor", "inspect_logs"],
                                "state": "persistent" if age > 60 else "new"
                            })
                    # Keys
                    for key_prefix, entries in list(ledger._key_ledger.items()):
                        score_sum = sum(s for s, _ in entries)
                        if score_sum >= 1.0:
                            is_blocked = score_sum >= ledger.threshold
                            severity = "critical" if is_blocked else "warning"
                            kind = "actor_blocked" if is_blocked else "high_threat_score"
                            last_ts = max(ts for _, ts in entries) if entries else time.time()
                            age = int(time.time() - last_ts)
                            attention.append({
                                "id": f"threat:key:{key_prefix}",
                                "kind": kind,
                                "severity": severity,
                                "confidence": round(min(1.0, score_sum / ledger.threshold), 2),
                                "blast_radius": f"key:{key_prefix}",
                                "age_sec": max(0, age),
                                "baseline_delta": f"+{int(score_sum * 100)}% delta",
                                "owner": "threat_ledger",
                                "suggested_actions": ["mute_actor", "inspect_logs"],
                                "state": "persistent" if age > 60 else "new"
                            })
            except Exception as e:
                logger.warning(f"Error scanning threat ledger for summary: {e}")

            # 2c. Empty Registry
            if not pool:
                attention.append({
                    "id": "registry:empty",
                    "kind": "empty_registry",
                    "severity": "critical",
                    "confidence": 1.0,
                    "blast_radius": "gateway",
                    "age_sec": int(uptime),
                    "baseline_delta": "N/A",
                    "owner": "registry",
                    "suggested_actions": ["add_endpoint"],
                    "state": "persistent"
                })

            # 2d. Budget alerts
            try:
                daily_limit = float(agent.config.get("budget", {}).get("daily_limit", 0.0))
                soft_limit = float(agent.config.get("budget", {}).get("soft_limit", 0.0))
                spent = float(getattr(agent, "total_cost_today", 0.0))
                if daily_limit > 0 and spent >= daily_limit:
                    attention.append({
                        "id": "budget:limit_exceeded",
                        "kind": "budget_exhausted",
                        "severity": "critical",
                        "confidence": 1.0,
                        "blast_radius": "all",
                        "age_sec": int(uptime),
                        "baseline_delta": f"+{int((spent - daily_limit)/daily_limit * 100)}% delta" if daily_limit > 0 else "N/A",
                        "owner": "budget",
                        "suggested_actions": ["increase_limit"],
                        "state": "persistent"
                    })
                elif soft_limit > 0 and spent >= soft_limit:
                    attention.append({
                        "id": "budget:soft_limit_exceeded",
                        "kind": "budget_warning",
                        "severity": "warning",
                        "confidence": 1.0,
                        "blast_radius": "all",
                        "age_sec": int(uptime),
                        "baseline_delta": f"+{int((spent - soft_limit)/soft_limit * 100)}% delta" if soft_limit > 0 else "N/A",
                        "owner": "budget",
                        "suggested_actions": ["increase_limit"],
                        "state": "persistent"
                    })
            except Exception as e:
                logger.warning(f"Error calculating budget alerts for summary: {e}")

            # Sort attention by severity (critical first) and confidence DESC
            severity_order = {"critical": 0, "warning": 1}
            attention.sort(key=lambda x: (severity_order.get(x["severity"], 2), -x["confidence"]))

            # 3. DO NEXT: Suggested actions and follow-up tasks
            do_next = []
            for item in attention:
                if item["kind"] in ("circuit_breaker_open", "circuit_breaker_half_open"):
                    ep = item["blast_radius"].replace("endpoint:", "")
                    do_next.append({
                        "id": f"task:reset_cb:{ep}",
                        "title": f"Reset circuit breaker for {ep}",
                        "description": f"Endpoint {ep} is offline or degraded. Reset it to CLOSED once the upstream is available.",
                        "action": "reset_cb",
                        "target": ep
                    })
                elif item["kind"] in ("high_threat_score", "actor_blocked"):
                    target = item["blast_radius"]
                    do_next.append({
                        "id": f"task:inspect_logs:{target}",
                        "title": f"Inspect logs for {target}",
                        "description": f"Actor {target} has elevated threat score. Inspect live logs for prompt injection attempts.",
                        "action": "inspect_logs",
                        "target": target
                    })
                elif item["kind"] == "empty_registry":
                    do_next.append({
                        "id": "task:add_endpoint",
                        "title": "Add a new endpoint",
                        "description": "Gateway has no active endpoints configured. Register an OpenAI, Ollama, or Anthropic provider.",
                        "action": "add_endpoint",
                        "target": ""
                    })
                elif item["kind"] in ("budget_exhausted", "budget_warning"):
                    do_next.append({
                        "id": "task:increase_limit",
                        "title": "Increase daily budget limit",
                        "description": "Today's spend is near or exceeds the daily cap. Increase budget.daily_limit in config.yaml.",
                        "action": "increase_limit",
                        "target": ""
                    })

            # Add default items if list is short
            if len(do_next) < 2:
                do_next.append({
                    "id": "task:review_budget",
                    "title": "Review daily budget usage",
                    "description": "System spend is within nominal limits. Review cost efficiency under the Analytics tab.",
                    "action": "view_analytics",
                    "target": ""
                })
                do_next.append({
                    "id": "task:check_plugins",
                    "title": "Inspect active plugins",
                    "description": "Ensure guards (PII, injection) are active and running under their target hooks.",
                    "action": "view_plugins",
                    "target": ""
                })

            # 4. RECENT CHANGES: config/plugin/endpoint/guard mutations
            recent_changes = []
            try:
                # Query last 5 entries from audit log
                audit_data = await agent.store.query_audit(limit=5)
                for item in audit_data.get("items", []):
                    desc = f"Request {item['req_id']} processed on {item['provider']}/{item['model']} (HTTP {item['status']})"
                    if item.get("blocked"):
                        desc = f"Blocked request {item['req_id']} on {item['model']}: {item['block_reason']}"
                    recent_changes.append({
                        "timestamp": item["ts"],
                        "type": "audit_block" if item.get("blocked") else "request",
                        "description": desc
                    })
            except Exception as e:
                logger.warning(f"Error querying recent audit logs for summary: {e}")

            # If no recent changes, add a default placeholder
            if not recent_changes:
                recent_changes.append({
                    "timestamp": int(time.time()),
                    "type": "system_boot",
                    "description": "System operational, waiting for incoming requests."
                })

            return {
                "now": now_data,
                "attention": attention,
                "do_next": do_next,
                "recent_changes": recent_changes
            }
        except Exception as e:
            logger.error("Dashboard summary generation failed", exc_info=True)
            raise HTTPException(status_code=500, detail="Internal server error")

    @router.get("/api/v1/slos")
    async def get_slos(request: Request):
        _check_admin_auth(request)
        
        # 1. Error Rates (SLOs) from circuit breakers
        circuits = await agent.circuit_manager.get_all_states()
        endpoints_slo = {}
        for ep, state in circuits.items():
            successes = state.get("success_count", 0)
            failures = state.get("failure_count", 0)
            total = successes + failures
            error_rate = (failures / total) if total > 0 else 0.0
            endpoints_slo[ep] = {
                "success_count": successes,
                "failure_count": failures,
                "error_rate": round(error_rate, 4),
                "circuit_state": state.get("state", "closed")
            }
            
        # 2. Budget Burn
        budget_cfg = agent.config.get("budget", {})
        daily_limit = budget_cfg.get("daily_limit", 50.0)
        spent = agent.total_cost_today
        burn_pct = (spent / daily_limit) if daily_limit > 0 else 0.0
        
        return {
            "budget": {
                "limit": daily_limit,
                "spent": round(spent, 6),
                "burn_percentage": round(burn_pct * 100, 2),
                "saturated": spent >= daily_limit
            },
            "upstream_error_rates": endpoints_slo
        }

    return router
