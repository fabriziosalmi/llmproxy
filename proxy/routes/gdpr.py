"""
GDPR compliance routes — Data Subject Rights.

Endpoints:
  POST /api/v1/gdpr/erase/{subject}   — Right to erasure (Article 17)
  GET  /api/v1/gdpr/export/{subject}  — Data Subject Access Request (Article 15)
  GET  /api/v1/gdpr/retention          — View retention policy
  POST /api/v1/gdpr/purge              — Manual trigger: purge expired records
"""

import json
import time
import logging
from fastapi import APIRouter, Request, HTTPException

logger = logging.getLogger("llmproxy.routes.gdpr")


def create_router(agent) -> APIRouter:
    router = APIRouter()

    def _check_admin_auth(request: Request):
        """Enforce API key auth on GDPR mutating endpoints.

        Unauthenticated access to erase/export allows any caller to delete or
        exfiltrate all subject data without a trace.  Auth is skipped only when
        explicitly disabled (development mode).
        """
        if not agent.config.get("server", {}).get("auth", {}).get("enabled", False):
            return
        auth_header = request.headers.get("Authorization", "")
        token = auth_header.replace("Bearer ", "").strip()
        valid_keys = agent._get_api_keys()
        if not token or token not in valid_keys:
            raise HTTPException(status_code=401, detail="GDPR: Unauthorized")

    @router.post("/api/v1/gdpr/erase/{subject}")
    async def erase_subject(subject: str, request: Request):
        """Right to erasure (GDPR Article 17)."""
        _check_admin_auth(request)
        # R2-09: Require minimum subject length to prevent broad matches
        # (e.g., subject="a" matching all session_ids starting with 'a').
        if len(subject) < 8:
            raise HTTPException(status_code=400, detail="Subject must be at least 8 characters")
        result = await agent.store.delete_subject_data(subject)
        total_deleted = sum(result.values())

        if total_deleted == 0:
            raise HTTPException(status_code=404, detail=f"No data found for subject '{subject}'")

        # Immutable audit of the erasure itself (GDPR requires this).
        # Use json.dumps — never interpolate subject directly into a JSON string
        # (subject may contain quotes/braces that break JSON structure).
        audit_meta = json.dumps({
            "action": "erase",
            "subject": subject,
            "deleted": total_deleted,
        }, separators=(",", ":"))
        await agent.store.log_audit(
            ts=int(time.time()),
            req_id=f"gdpr-erase-{subject[:16]}",
            session_id="GDPR_SYSTEM",
            key_prefix="GDPR",
            model="",
            provider="",
            status=204,
            prompt_tokens=0,
            completion_tokens=0,
            cost_usd=0.0,
            latency_ms=0.0,
            blocked=False,
            block_reason="",
            metadata=audit_meta,
        )

        logger.info(f"GDPR erasure: subject='{subject}' deleted={result}")
        return {
            "status": "erased",
            "subject": subject,
            **result,
        }

    @router.get("/api/v1/gdpr/export/{subject}")
    async def export_subject(subject: str, request: Request):
        """Data Subject Access Request (GDPR Article 15).

        Returns all data associated with the subject, scrubbed of
        sensitive fields (API keys, tokens). Response is JSON.
        """
        _check_admin_auth(request)
        if len(subject) < 8:
            raise HTTPException(status_code=400, detail="Subject must be at least 8 characters")
        data = await agent.store.export_subject_data(subject)
        total_records = len(data.get("audit", [])) + len(data.get("spend", [])) + len(data.get("roles", []))

        if total_records == 0:
            raise HTTPException(status_code=404, detail=f"No data found for subject '{subject}'")

        # Scrub sensitive fields from export
        from core.export import scrub_dict
        scrubbed_audit = [scrub_dict(r) for r in data.get("audit", [])]
        scrubbed_spend = [scrub_dict(r) for r in data.get("spend", [])]

        return {
            "subject": subject,
            "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "record_count": total_records,
            "audit_log": scrubbed_audit,
            "spend_log": scrubbed_spend,
            "identity": data.get("roles", []),
        }

    @router.get("/api/v1/gdpr/retention")
    async def retention_policy():
        """View the configured data retention policy."""
        gdpr_cfg = agent.config.get("gdpr", {})
        return {
            "retention_days": gdpr_cfg.get("retention_days", 90),
            "auto_purge": gdpr_cfg.get("auto_purge", True),
            "purposes": [
                "security_monitoring",
                "cost_tracking",
                "audit_compliance",
            ],
            "legal_basis": "Article 6(1)(f) — Legitimate Interest (Security)",
            "data_categories": [
                "session_id",
                "api_key_prefix",
                "model_usage",
                "cost_data",
            ],
        }

    @router.post("/api/v1/gdpr/purge")
    async def manual_purge(request: Request):
        """Manual trigger: purge records older than retention period."""
        _check_admin_auth(request)
        gdpr_cfg = agent.config.get("gdpr", {})
        retention_days = gdpr_cfg.get("retention_days", 90)
        result = await agent.store.purge_expired(retention_days)

        logger.info(f"GDPR purge: retention={retention_days}d deleted={result}")
        return {
            "status": "purged",
            "retention_days": retention_days,
            **result,
        }

    return router
