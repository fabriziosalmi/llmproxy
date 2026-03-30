"""
Startup validation — catch config errors before the proxy starts.

Runs immediately after config load and provides actionable error messages
instead of cryptic KeyError/ValueError tracebacks.
"""

import os
import sys
import logging

logger = logging.getLogger("llmproxy.startup")


class StartupError(Exception):
    """Raised when a critical configuration issue prevents startup."""


def validate_config(config: dict) -> list[str]:
    """Validate config and return warnings. Raises StartupError on critical issues."""
    warnings = []

    # 1. Auth keys (required)
    auth_cfg = config.get("server", {}).get("auth", {})
    if auth_cfg.get("enabled", True):
        keys_env = auth_cfg.get("api_keys_env", "LLM_PROXY_API_KEYS")
        keys_val = os.environ.get(keys_env, "")
        if not keys_val or keys_val == "sk-proxy-CHANGE-ME":
            raise StartupError(
                f"Proxy authentication requires {keys_env} to be set.\n"
                f"  1. Edit .env and set: {keys_env}=sk-proxy-<your-key>\n"
                f"  2. Generate a key: python -c \"import secrets; print(f'sk-proxy-{{secrets.token_hex(16)}}')\"\n"
                f"  3. Restart the proxy"
            )

    # 2. At least one endpoint configured
    endpoints = config.get("endpoints", {})
    if not endpoints:
        raise StartupError(
            "No LLM endpoints configured in config.yaml.\n"
            "  Add at least one provider under 'endpoints:'.\n"
            "  Quickstart: cp config.minimal.yaml config.yaml"
        )

    # 3. Provider API keys
    active_providers = []
    for name, ep_cfg in endpoints.items():
        api_key_env = ep_cfg.get("api_key_env")
        auth_type = ep_cfg.get("auth_type", "bearer")

        if auth_type == "none":
            active_providers.append(name)
            continue

        if api_key_env:
            val = os.environ.get(api_key_env, "")
            if val and not val.startswith(("sk-proj-...", "sk-ant-...", "AIza...", "gsk_...")):
                active_providers.append(name)
            elif not val:
                warnings.append(
                    f"Endpoint '{name}' needs {api_key_env} — skipped.\n"
                    f"  Set in .env: {api_key_env}=your-api-key"
                )

    if not active_providers:
        raise StartupError(
            "No endpoints have valid API keys configured.\n"
            "  Set at least one provider key in .env. Example:\n"
            "    OPENAI_API_KEY=sk-proj-...\n"
            "  Then restart the proxy."
        )

    # 4. Config file sanity
    server_cfg = config.get("server", {})
    port = server_cfg.get("port", 8090)
    if not isinstance(port, int) or port < 1 or port > 65535:
        raise StartupError(f"Invalid server port: {port}. Must be 1-65535.")

    # 5. Security config
    security_cfg = config.get("security", {})
    max_payload = security_cfg.get("max_payload_size_kb", 512)
    if max_payload < 1:
        warnings.append("security.max_payload_size_kb < 1 KB — most requests will be rejected")

    # 6. Caching config
    cache_cfg = config.get("caching", {})
    if cache_cfg.get("enabled") and not cache_cfg.get("db_path"):
        warnings.append("Caching enabled but no db_path set — using 'cache.db'")

    return warnings


def run_startup_checks(config: dict):
    """Run all startup validations. Exits with clear message on failure."""
    try:
        warnings = validate_config(config)
        for w in warnings:
            logger.warning(f"CONFIG: {w}")
        if warnings:
            logger.info(f"Startup checks passed with {len(warnings)} warning(s)")
        else:
            logger.info("Startup checks passed")
    except StartupError as e:
        logger.critical(f"\n{'='*60}\n  STARTUP FAILED\n{'='*60}\n\n{e}\n")
        sys.exit(1)
