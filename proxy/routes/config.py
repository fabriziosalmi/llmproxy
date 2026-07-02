"""Config routes: view (yaml/warnings) + edit (raw/validate/apply).

Split out of the monolithic admin.py so the config-management surface — which is
security-sensitive (it rewrites config.yaml and hot-reloads the proxy) — lives in
one cohesive, independently-testable module.
"""

import logging

from fastapi import APIRouter, Request, HTTPException

logger = logging.getLogger("llmproxy.routes.config")

# Editing targets the on-disk config.yaml *source* (env-ref based, no inline
# secrets) — NOT the runtime-merged /config/yaml view, which is redacted and
# would round-trip "***" back over real values.
_MAX_CONFIG_BYTES = 256 * 1024


def create_router(agent) -> APIRouter:
    router = APIRouter()

    def _check_admin_auth(request: Request):
        """Enforce API key / JWT auth on mutating admin endpoints when auth is on."""
        if not agent.config.get("server", {}).get("auth", {}).get("enabled", False):
            return  # Auth disabled — development mode, allow all
        from proxy.auth_helpers import parse_bearer

        token = parse_bearer(request.headers.get("Authorization", ""))

        if hasattr(agent, "jwt_authenticator") and agent.jwt_authenticator.enabled:
            if not agent.jwt_authenticator.verify_token(token):
                raise HTTPException(status_code=401, detail="Admin: Unauthorized (Invalid JWT)")
            return

        if not agent._verify_admin_key(token):
            raise HTTPException(status_code=401, detail="Admin: Unauthorized")

    def _validate_config_text(text: str):
        """Parse + validate a proposed config. Returns (parsed_or_None, errors, warnings)."""
        import yaml as _yaml

        try:
            parsed = _yaml.safe_load(text)
        except _yaml.YAMLError as exc:
            return None, [f"YAML parse error: {exc}"], []
        if not isinstance(parsed, dict):
            return None, ["Config root must be a mapping (key: value), not a list or scalar."], []
        from core.startup_checks import validate_config, StartupError

        errors: list[str] = []
        warnings: list[str] = []
        try:
            warnings = validate_config(parsed) or []
        except StartupError as exc:
            errors.append(str(exc))
        except Exception as exc:  # noqa: BLE001 — a validator bug must not 500 the editor
            errors.append(f"Validation error: {exc}")
        return parsed, errors, warnings

    async def _reload_from_disk():
        """Re-read config.yaml and re-init config-dependent subsystems in place."""
        agent.config = agent._load_config()
        agent._config_hash = agent._compute_config_hash_sync()
        from core.webhooks import WebhookDispatcher

        old_webhooks = getattr(agent, "webhooks", None)
        new_webhooks = WebhookDispatcher(agent.config)
        agent.webhooks = new_webhooks
        if old_webhooks and old_webhooks is not new_webhooks:
            try:
                await old_webhooks.close()
            except Exception:
                logger.warning("Previous webhook dispatcher close failed", exc_info=True)
        from core.security import SecurityShield

        agent.security = SecurityShield(agent.config, assistant=agent.security.assistant)

    @router.get("/api/v1/config/yaml")
    async def get_config_yaml(request: Request):
        """Return the active config rendered as YAML, with secrets redacted."""
        _check_admin_auth(request)
        import yaml as _yaml
        from core.export import scrub_dict

        try:
            redacted = scrub_dict(agent.config or {})
            text = _yaml.safe_dump(redacted, default_flow_style=False, sort_keys=False)
        except Exception as e:  # noqa: BLE001 — surface, don't crash the route
            logger.error(f"YAML serialisation failed: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="YAML serialisation failed") from e
        return {"yaml": text}

    @router.get("/api/v1/config/warnings")
    async def get_config_warnings(request: Request):
        """Surface startup-validation warnings to the admin UI."""
        _check_admin_auth(request)
        from core.startup_checks import get_startup_warnings

        return {"warnings": get_startup_warnings()}

    @router.get("/api/v1/config/raw")
    async def get_config_raw(request: Request):
        """Return the raw on-disk config.yaml *source* for the editor (admin-only)."""
        _check_admin_auth(request)
        try:
            with open(agent.config_path, "r") as f:
                text = f.read()
        except FileNotFoundError:
            text = ""
        except Exception as e:  # noqa: BLE001
            logger.error(f"Reading config source failed: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="Could not read config source") from e
        return {"yaml": text, "path": agent.config_path}

    @router.post("/api/v1/config/validate")
    async def validate_config_endpoint(request: Request):
        """Dry-run validate a proposed config without writing anything (admin-only)."""
        _check_admin_auth(request)
        body = await request.json()
        text = body.get("yaml", "")
        if not isinstance(text, str):
            raise HTTPException(status_code=400, detail="`yaml` must be a string")
        if len(text.encode("utf-8")) > _MAX_CONFIG_BYTES:
            raise HTTPException(status_code=413, detail="Config too large")
        _parsed, errors, warnings = _validate_config_text(text)
        return {"valid": not errors, "errors": errors, "warnings": warnings}

    @router.post("/api/v1/config/apply")
    async def apply_config_endpoint(request: Request):
        """Validate, back up, atomically write, and hot-reload a new config (admin-only)."""
        import os
        import time as _time
        import tempfile

        _check_admin_auth(request)
        body = await request.json()
        text = body.get("yaml", "")
        if not isinstance(text, str):
            raise HTTPException(status_code=400, detail="`yaml` must be a string")
        if len(text.encode("utf-8")) > _MAX_CONFIG_BYTES:
            raise HTTPException(status_code=413, detail="Config too large")

        _parsed, errors, warnings = _validate_config_text(text)
        if errors:
            # Never write an invalid config — return the reasons for the editor.
            raise HTTPException(status_code=400, detail={"errors": errors, "warnings": warnings})

        abspath = os.path.abspath(agent.config_path)
        directory = os.path.dirname(abspath) or "."
        try:
            with open(abspath, "r") as f:
                previous = f.read()
        except FileNotFoundError:
            previous = ""

        # Timestamped backup so a bad apply is always recoverable on disk.
        backup_path = f"{abspath}.bak.{int(_time.time())}"
        try:
            if previous:
                with open(backup_path, "w") as f:
                    f.write(previous)
            # Atomic replace via temp file in the same dir (same filesystem).
            fd, tmp = tempfile.mkstemp(dir=directory, prefix=".config.", suffix=".tmp")
            with os.fdopen(fd, "w") as f:
                f.write(text)
            os.replace(tmp, abspath)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Config write failed: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="Config write failed") from e

        # Hot-reload; on any failure restore the previous file and reload it back.
        try:
            await _reload_from_disk()
        except Exception as e:  # noqa: BLE001
            logger.error(f"Reload after config apply failed, rolling back: {e}", exc_info=True)
            try:
                with open(abspath, "w") as f:
                    f.write(previous)
                await _reload_from_disk()
            except Exception:
                logger.error("Rollback reload also failed", exc_info=True)
            await agent._add_log(
                "SECURITY: Config apply FAILED and was rolled back", level="SECURITY"
            )
            raise HTTPException(
                status_code=500, detail="New config failed to load — rolled back"
            ) from e

        await agent._add_log(
            f"SECURITY: Config applied via Admin UI (backup: {os.path.basename(backup_path)})",
            level="SECURITY",
        )
        return {"applied": True, "warnings": warnings, "backup": os.path.basename(backup_path)}

    return router
