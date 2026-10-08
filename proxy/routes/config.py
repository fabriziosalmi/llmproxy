"""Config routes: view (yaml/warnings) + edit (raw/validate/apply).

Split out of the monolithic admin.py so the config-management surface — which is
security-sensitive (it rewrites config.yaml and hot-reloads the proxy) — lives in
one cohesive, independently-testable module.
"""

import hashlib
import hmac
import logging
import os
import secrets
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from core.atomic_io import atomic_write as _atomic_write
from core.auth_policy import auth_enabled
from proxy.routes.deps import ConfigAgent

logger = logging.getLogger("llmproxy.routes.config")

# Editing targets the on-disk config.yaml *source* (env-ref based, no inline
# secrets) — NOT the runtime-merged /config/yaml view, which is redacted and
# would round-trip "***" back over real values.
_MAX_CONFIG_BYTES = 256 * 1024

# ── Dangerous-delta governance (issue #108) ──────────────────────────────
#
# An admin bearer can rewrite config.yaml live. Most keys are operational
# (routing, aliases, budgets) — but a few deltas lower the security posture
# itself: disabling auth, disabling the firewall, clearing the domain
# blocklist, or widening the payload cap dramatically. Those need an explicit
# second step: a short-lived confirm token bound to the exact proposed text.
#
# Honest scope: this is a confirmation-of-intent + audit gate, NOT a second
# privilege tier. A stolen admin bearer can still mint a token (two requests
# instead of one). What it stops is the one-click/one-request foot-gun — a
# templated apply, a UI misclick, an automation pushing a config that happens
# to flip `enabled: false` — and it leaves a named audit trail either way.
# Real privilege separation stays where it is: segregated admin keys,
# rotation, and never exposing the control plane without auth.
_CONFIRM_TTL_S = 120
_CONFIRM_MAX_TTL_S = 600
_CONFIRM_USED_MAX = 1024


# Last-resort confirm-token secret, generated per process. Same trade-off as
# the SSE fallback in telemetry.py: a restart invalidates outstanding tokens,
# and tokens live at most 600 seconds anyway.
_FALLBACK_CONFIRM_SECRET = secrets.token_urlsafe(32)


def _nested(cfg: object, *keys: str):
    """Safe nested dict lookup; returns None when any level is absent."""
    cur = cfg
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _config_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _dangerous_deltas(old: dict, new: dict) -> list:
    """Posture-lowering deltas between the live config and a proposed one.

    Each entry is a short machine-readable id (also human-readable in the
    403 response and the audit log). Only *transitions* from strict to lax
    count — an already-disabled guard staying disabled is not a delta.
    """
    deltas = []

    if _nested(old, "server", "auth", "enabled") is True and _nested(
        new, "server", "auth", "enabled"
    ) is False:
        deltas.append("auth-disabled")

    # Absent firewall flag defaults to enabled (app_factory), so only an
    # explicit `false` in the proposal counts.
    if _nested(old, "security", "firewall", "enabled") is not False and _nested(
        new, "security", "firewall", "enabled"
    ) is False:
        deltas.append("firewall-disabled")

    old_blocked = (
        _nested(old, "security", "link_sanitization", "blocked_domains") or []
    )
    new_blocked = (
        _nested(new, "security", "link_sanitization", "blocked_domains") or []
    )
    if old_blocked and not new_blocked:
        deltas.append("blocklist-cleared")

    old_cap = _nested(old, "security", "max_payload_size_kb")
    new_cap = _nested(new, "security", "max_payload_size_kb")
    if (
        isinstance(old_cap, (int, float))
        and not isinstance(old_cap, bool)
        and isinstance(new_cap, (int, float))
        and not isinstance(new_cap, bool)
        and old_cap > 0
        and new_cap > old_cap * 4
    ):
        deltas.append("payload-widened")

    return deltas


def create_router(agent: ConfigAgent) -> APIRouter:
    router = APIRouter()

    def _check_admin_auth(request: Request):
        """Enforce API key / JWT auth on mutating admin endpoints when auth is on."""
        if not auth_enabled(agent.config):
            return  # Auth disabled — development mode, allow all
        from proxy.auth_helpers import parse_bearer, principal_already_verified

        # The middleware ran and admitted this caller — possibly on a JWT or an
        # SSO identity, which the key check below would refuse. Defer to its
        # verdict; this closure stays as the check for requests that somehow
        # reached the handler without passing it.
        if principal_already_verified(request):
            return

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
            # Never echo raw exception text to the client: CodeQL
            # py/stack-trace-exposure (and good hygiene) — the admin just sent
            # this text, so they can locate the break; the detail goes to logs.
            logger.error("Config YAML parse failed: %s", exc, exc_info=True)
            return (
                None,
                ["YAML parse error: the proposed text is not valid YAML."],
                [],
            )
        if not isinstance(parsed, dict):
            return None, ["Config root must be a mapping (key: value), not a list or scalar."], []
        from core.startup_checks import StartupError, validate_config

        errors: list[str] = []
        warnings: list[str] = []
        try:
            warnings = validate_config(parsed) or []
        except StartupError as exc:
            errors.append(str(exc))
        except Exception as exc:  # noqa: BLE001 — a validator bug must not 500 the editor
            logger.error("Config validator bug: %s", exc, exc_info=True)
            errors.append("Validation error: internal validator failure — see server logs.")
        return parsed, errors, warnings

    # Single-use confirm tokens, bound to a proposed config hash. Kept in
    # closure state (per process, like the SSE token fallback): a restart
    # invalidates outstanding tokens, which is the safe direction.
    _used_confirm_tokens: set = set()

    def _confirm_secret() -> str:
        override = _nested(agent.config, "security", "confirm", "signing_secret")
        if override:
            return str(override)
        # Never an API key (see the SSE token secret): the confirm token gates
        # config changes that lower the security posture, and an inference key is
        # the lowest credential issued. Per-process random; a restart drops
        # outstanding tokens, which is the safe direction.
        return _FALLBACK_CONFIRM_SECRET

    def _mint_confirm_token(config_sha: str, ttl_s: int = _CONFIRM_TTL_S) -> str:
        exp = int(time.time()) + max(10, min(ttl_s, _CONFIRM_MAX_TTL_S))
        nonce = secrets.token_hex(8)
        payload = f"{exp}.{config_sha}.{nonce}"
        sig = hmac.new(
            _confirm_secret().encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        return f"{payload}.{sig}"

    def _verify_and_consume(token: str, config_sha: str) -> bool:
        """Single-use verify: valid shape, signature, TTL, hash binding."""
        parts = (token or "").split(".")
        if len(parts) != 4:
            return False
        exp_s, sha, nonce, sig = parts
        if (
            not exp_s.isdigit()
            or len(sha) != 64
            or len(nonce) < 8
            or len(sig) != 64
            or not hmac.compare_digest(sha, config_sha)
        ):
            return False
        remaining = int(exp_s) - int(time.time())
        if remaining < 0 or remaining > _CONFIRM_MAX_TTL_S:
            return False
        payload = f"{exp_s}.{sha}.{nonce}"
        expected = hmac.new(
            _confirm_secret().encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, sig):
            return False
        if token in _used_confirm_tokens:
            return False
        _used_confirm_tokens.add(token)
        # Bound the set: drop entries once it grows past the cap. Tokens are
        # self-expiring, so evicting oldest-first needs no bookkeeping — a
        # plain clear is fine and fails closed (outstanding tokens die).
        if len(_used_confirm_tokens) > _CONFIRM_USED_MAX:
            _used_confirm_tokens.clear()
        return True

    def _acting_principal(request: Request) -> str:
        """Short, non-secret identifier of the caller for the audit trail."""
        from proxy.auth_helpers import parse_bearer, principal_already_verified

        if principal_already_verified(request):
            return "sso/jwt-principal"
        token = parse_bearer(request.headers.get("Authorization", ""))
        if token:
            return f"key:{token[:8]}..."
        return "dev-open"

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
            with open(agent.config_path) as f:
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
        dangerous = (
            _dangerous_deltas(agent.config, _parsed)
            if not errors and isinstance(_parsed, dict)
            else []
        )
        return {
            "valid": not errors,
            "errors": errors,
            "warnings": warnings,
            "dangerous_deltas": dangerous,
        }

    @router.post("/api/v1/config/confirm-token")
    async def confirm_token_endpoint(request: Request):
        """Mint a single-use confirm token for a posture-lowering apply.

        Body: {"yaml": "<proposed config>"}. The token is bound to the
        SHA-256 of that exact text and expires after ~120s. Minting itself is
        audit-logged, so a stolen bearer minting tokens leaves traces too.
        """
        _check_admin_auth(request)
        body = await request.json()
        text = body.get("yaml", "")
        if not isinstance(text, str):
            raise HTTPException(status_code=400, detail="`yaml` must be a string")
        if len(text.encode("utf-8")) > _MAX_CONFIG_BYTES:
            raise HTTPException(status_code=413, detail="Config too large")
        _parsed, errors, _warnings = _validate_config_text(text)
        if errors:
            raise HTTPException(
                status_code=400,
                detail=f"Config validation failed: {len(errors)} error(s)",
            )
        deltas = (
            _dangerous_deltas(agent.config, _parsed)
            if isinstance(_parsed, dict)
            else []
        )
        if not deltas:
            raise HTTPException(
                status_code=400,
                detail="No dangerous deltas in the proposed config — apply directly.",
            )
        principal = _acting_principal(request)
        await agent._add_log(
            f"SECURITY: Confirm token minted by {principal} for deltas: "
            + ", ".join(deltas),
            level="SECURITY",
        )
        return {
            "confirm_token": _mint_confirm_token(_config_sha256(text)),
            "expires_in": _CONFIRM_TTL_S,
            "deltas": deltas,
        }

    @router.post("/api/v1/config/apply")
    async def apply_config_endpoint(request: Request):
        """Validate, back up, atomically write, and hot-reload a new config (admin-only)."""
        import time as _time

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
            #
            # `detail` is a string here, as it is on the other 86 raise sites in
            # this package. This was the one place that made it an object, so no
            # client could parse the error envelope uniformly: rendering
            # `detail` printed [object Object] here and read correctly
            # everywhere else, while reading `detail.errors` did the reverse.
            # The structured reasons move up one level, where they are still
            # available and no longer change the shape of a shared field.
            return JSONResponse(
                status_code=400,
                content={
                    "detail": f"Config validation failed: {len(errors)} error(s)",
                    "errors": errors,
                    "warnings": warnings,
                },
            )

        # Dangerous-delta gate (issue #108): posture-lowering transitions need
        # a single-use confirm token bound to this exact text. Without it the
        # attempt is rejected AND audit-logged — a silent probe for how far an
        # admin bearer reaches must leave a trace.
        deltas = (
            _dangerous_deltas(agent.config, _parsed)
            if isinstance(_parsed, dict)
            else []
        )
        principal = _acting_principal(request)
        if deltas:
            token = body.get("confirm_token", "")
            if not isinstance(token, str) or not _verify_and_consume(
                token, _config_sha256(text)
            ):
                await agent._add_log(
                    f"SECURITY: Dangerous config apply REJECTED for {principal} "
                    f"(missing/invalid confirm token) — deltas: " + ", ".join(deltas),
                    level="SECURITY",
                )
                return JSONResponse(
                    status_code=403,
                    content={
                        "detail": "Dangerous config deltas require a confirm token. "
                        "Mint one via POST /api/v1/config/confirm-token with the "
                        "same `yaml`, then retry apply with `confirm_token`.",
                        "confirm_required": True,
                        "deltas": deltas,
                    },
                )

        abspath = os.path.abspath(agent.config_path)
        directory = os.path.dirname(abspath) or "."
        try:
            with open(abspath) as f:
                previous = f.read()
        except FileNotFoundError:
            previous = ""

        # Timestamped backup so a bad apply is always recoverable on disk.
        #
        # The backup itself is written atomically, and fsynced before the rename.
        # It used to be a plain truncating write: interrupted halfway it left a
        # .bak holding a prefix of the old config — and a truncated YAML
        # document frequently still parses, so restoring it would silently drop
        # whatever came after the cut. The artefact the recovery story depends
        # on was the one write here that could tear.
        backup_path = f"{abspath}.bak.{int(_time.time())}"
        try:
            if previous:
                _atomic_write(previous, backup_path, directory, ".config.bak.")
            _atomic_write(text, abspath, directory, ".config.")
        except Exception as e:  # noqa: BLE001
            logger.error(f"Config write failed: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="Config write failed") from e

        # Hot-reload; on any failure restore the previous file and reload it back.
        try:
            await _reload_from_disk()
        except Exception as e:  # noqa: BLE001
            logger.error(f"Reload after config apply failed, rolling back: {e}", exc_info=True)
            try:
                _atomic_write(previous, abspath, directory, ".config.rollback.")
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
            f"SECURITY: Config applied via Admin UI by {principal} "
            f"(backup: {os.path.basename(backup_path)})"
            + (f" — dangerous deltas confirmed: {', '.join(deltas)}" if deltas else ""),
            level="SECURITY",
        )
        return {"applied": True, "warnings": warnings, "backup": os.path.basename(backup_path)}

    return router
