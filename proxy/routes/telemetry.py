"""Telemetry routes: health, metrics, logs SSE stream, client log ingest."""
import re
import json
import asyncio

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse, Response, StreamingResponse

# Strip ANSI escape sequences and control chars to prevent terminal injection via xterm.js
_ANSI_RE = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]|\x1b\].*?\x07')
_CTRL_RE = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')

# SSE connection limit — prevents resource exhaustion from stale consumers
_MAX_SSE_CONNECTIONS = 20
_active_log_streams = 0

def _sanitize_log(log: dict) -> dict:
    """Strip terminal escape sequences from log string values."""
    sanitized = {}
    for k, v in log.items():
        if isinstance(v, str):
            v = _ANSI_RE.sub('', v)
            v = _CTRL_RE.sub('', v)
        elif isinstance(v, dict):
            v = _sanitize_log(v)
        sanitized[k] = v
    return sanitized

def create_router(agent) -> APIRouter:
    router = APIRouter()

    def _check_auth(request: Request):
        """Enforce API key auth on sensitive streaming endpoints.

        /api/v1/logs streams all application events including SECURITY-level
        entries (blocked IPs, failed auth attempts, identity assertions, plugin
        installs).  An unauthenticated subscriber gets a real-time recon feed
        that reveals which keys are failing (to time attacks), which IPs are
        blocked (to rotate), and user email addresses from IDENTITY log lines.
        Auth is skipped only when explicitly disabled (development mode).
        """
        if not agent.config.get("server", {}).get("auth", {}).get("enabled", False):
            return
        auth_header = request.headers.get("Authorization", "")
        token = auth_header.replace("Bearer ", "").strip()
        valid_keys = agent._get_api_keys()
        if not token or token not in valid_keys:
            raise HTTPException(status_code=401, detail="Telemetry: Unauthorized")

    @router.get("/health")
    async def health():
        """Per-component health (M.3).

        Top-level fields preserved for backward compat with existing pollers.
        New `components` block surfaces per-subsystem state so an operator can
        see which piece is degrading instead of waiting for the whole proxy to
        tip. The HTTP status stays 200 always — overall health is in the body.
        Status semantics:
          ok       — fully operational
          degraded — works but something's off (cache disabled, all circuits OPEN, log queue saturated)
          down     — critical subsystem is unreachable (store, session)
        """
        import time as _time
        components: dict = {}

        # Endpoints + circuit breakers — coalesce both into a single component
        # since they're conceptually "upstream availability".
        pool = []
        healthy_count = 0
        circuits_open = 0
        try:
            pool = await agent.store.get_pool()
            for e in pool:
                if await (await agent.circuit_manager.get_breaker(e.id)).can_execute():
                    healthy_count += 1
            circuit_states = agent.circuit_manager.get_all_states()
            circuits_open = sum(1 for s in circuit_states.values() if s.get("state") == "open")
            ep_status = "ok"
            if pool and healthy_count == 0:
                ep_status = "degraded"  # Pool exists but every endpoint is gated
            elif circuits_open > 0:
                ep_status = "degraded"
            components["endpoints"] = {
                "status": ep_status,
                "total": len(pool),
                "healthy": healthy_count,
                "circuits_open": circuits_open,
            }
        except Exception as exc:  # noqa: BLE001 — surface, don't crash /health
            components["endpoints"] = {"status": "down", "detail": str(exc)[:120]}

        # Store — touch a state read so we exercise the actual DB path,
        # not just object existence.
        try:
            _ = await agent.store.get_state("proxy_enabled", True)
            components["store"] = {"status": "ok"}
        except Exception as exc:
            components["store"] = {"status": "down", "detail": str(exc)[:120]}

        # Cache — disabled is "degraded" (we can serve, but slower); a stats()
        # exception is "down".
        try:
            if not getattr(agent.cache_backend, "_enabled", False):
                components["cache"] = {"status": "degraded", "detail": "cache disabled in config"}
            else:
                stats = await agent.cache_backend.stats() if callable(getattr(agent.cache_backend, "stats", None)) else {}
                components["cache"] = {"status": "ok", **(stats if isinstance(stats, dict) else {})}
        except Exception as exc:
            components["cache"] = {"status": "down", "detail": str(exc)[:120]}

        # Plugins — count loaded + per-ring sizes. Empty rings is a config
        # state, not a fault, so always "ok" unless the manager itself errors.
        try:
            rings = getattr(agent.plugin_manager, "rings", {})
            ring_count = {hook.value if hasattr(hook, "value") else str(hook): len(plugins)
                          for hook, plugins in rings.items()}
            instances = getattr(agent.plugin_manager, "_plugin_instances", {})
            components["plugins"] = {
                "status": "ok",
                "loaded": len(instances),
                "ring_count": ring_count,
            }
        except Exception as exc:
            components["plugins"] = {"status": "down", "detail": str(exc)[:120]}

        # Upstream HTTP session (aiohttp) — required for any forward.
        sess = getattr(agent, "_session", None)
        session_active = sess is not None and not getattr(sess, "closed", True)
        components["session"] = {"status": "ok" if session_active else "down"}

        # Log queue — high saturation means we're about to start dropping
        # to DLQ on the hot path.
        try:
            log_q = getattr(agent, "log_queue", None)
            if log_q is not None:
                depth = log_q.qsize() if hasattr(log_q, "qsize") else 0
                maxsize = getattr(log_q, "maxsize", 0) or 0
                saturation = (depth / maxsize) if maxsize else 0.0
                lq_status = "degraded" if saturation >= 0.8 else "ok"
                components["log_queue"] = {
                    "status": lq_status,
                    "depth": depth,
                    "max": maxsize,
                    "saturation": round(saturation, 3),
                }
            else:
                components["log_queue"] = {"status": "ok", "detail": "no queue attached"}
        except Exception as exc:
            components["log_queue"] = {"status": "down", "detail": str(exc)[:120]}

        # Compute overall — store + session are critical (no proxy without them).
        critical = {"store", "session"}
        overall = "ok"
        for name, comp in components.items():
            s = comp.get("status", "ok")
            if s == "down" and name in critical:
                overall = "down"
                break
            if s in ("down", "degraded") and overall == "ok":
                overall = "degraded"

        uptime = _time.time() - getattr(agent, "_start_time", _time.time())
        return {
            # Backward-compat top-level fields — existing pollers keep working.
            "status": overall,
            "version": getattr(agent, "_version", "unknown"),
            "uptime_seconds": round(uptime),
            "pool_size": len(pool),
            "pool_healthy": healthy_count,
            "session_active": session_active,
            "budget_today_usd": round(getattr(agent, "total_cost_today", 0.0), 4),
            # New per-subsystem block.
            "components": components,
        }

    @router.get("/metrics")
    async def metrics():
        from core.metrics import get_metrics_response
        body, content_type = get_metrics_response()
        return Response(content=body, media_type=content_type)

    @router.get("/api/v1/logs")
    async def stream_logs(request: Request):
        _check_auth(request)
        global _active_log_streams
        if _active_log_streams >= _MAX_SSE_CONNECTIONS:
            raise HTTPException(status_code=503, detail="Too many SSE connections")

        async def log_generator():
            global _active_log_streams
            _active_log_streams += 1
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        log = await asyncio.wait_for(agent.log_queue.get(), timeout=1.0)
                        yield f"data: {json.dumps(_sanitize_log(log) if isinstance(log, dict) else log)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keep-alive\n\n"
                    except (asyncio.CancelledError, GeneratorExit):
                        break
            except (asyncio.CancelledError, GeneratorExit):
                pass
            finally:
                _active_log_streams -= 1

        return StreamingResponse(log_generator(), media_type="text/event-stream")

    # ── Client-log ingest ──────────────────────────────────────────────────
    # The browser logger (ui/src/services/logger.ts → backendSink) batches
    # up to ~50 records and POSTs them here on debounce + sendBeacon at
    # pagehide. We want the records in agent._add_log so the existing SSE
    # surface, DLQ overflow, and operator log view all show client errors
    # alongside server events. Auth + global rate limit already guard this
    # path; we additionally cap batch size and message length to limit
    # damage if a token leaks.
    _MAX_RECORDS_PER_BATCH = 100
    _MAX_MESSAGE_LEN = 4096
    _ALLOWED_LEVELS = {"DEBUG", "INFO", "WARN", "WARNING", "ERROR", "CRITICAL"}

    @router.post("/api/v1/logs/client")
    async def ingest_client_logs(request: Request):
        _check_auth(request)
        cfg = agent.config.get("security", {}).get("client_logs", {})
        if cfg.get("enabled") is False:
            raise HTTPException(status_code=404, detail="Client log ingest disabled")

        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="Body must be an object")

        records = body.get("records") or []
        if not isinstance(records, list):
            raise HTTPException(status_code=400, detail="'records' must be an array")
        if len(records) > _MAX_RECORDS_PER_BATCH:
            raise HTTPException(
                status_code=413,
                detail=f"Batch exceeds {_MAX_RECORDS_PER_BATCH} records",
            )

        session = body.get("session")
        if session is not None and not isinstance(session, str):
            session = None
        elif isinstance(session, str):
            session = session[:64]

        accepted = 0
        dropped = 0
        for rec in records:
            if not isinstance(rec, dict):
                dropped += 1
                continue
            level_raw = str(rec.get("level", "INFO")).upper()
            level = level_raw if level_raw in _ALLOWED_LEVELS else "INFO"
            if level == "WARN":
                level = "WARNING"
            message = str(rec.get("message", ""))[:_MAX_MESSAGE_LEN]
            if not message:
                dropped += 1
                continue
            ctx_raw = rec.get("context") if isinstance(rec.get("context"), dict) else rec.get("ctx")
            ctx = ctx_raw if isinstance(ctx_raw, dict) else None
            metadata: dict = {"source": "client"}
            if session:
                metadata["session"] = session
            if isinstance(rec.get("ts"), (int, float)):
                metadata["client_ts"] = rec["ts"]
            if ctx:
                metadata["ctx"] = ctx
            sanitized = _sanitize_log({"message": message, "metadata": metadata})
            try:
                await agent._add_log(
                    f"CLIENT: {sanitized['message']}",
                    level=level,
                    metadata=sanitized.get("metadata") or metadata,
                )
                accepted += 1
            except Exception:
                dropped += 1

        return JSONResponse(
            status_code=202,
            content={"accepted": accepted, "dropped": dropped},
        )

    return router
