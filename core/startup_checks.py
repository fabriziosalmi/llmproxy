"""
Startup validation — catch config errors before the proxy starts.

Runs immediately after config load and provides actionable error messages
instead of cryptic KeyError/ValueError tracebacks. The warnings list is
also stashed at module scope so admin routes can surface it in the UI
(see /api/v1/config/warnings).
"""

import os
import sys
import logging

logger = logging.getLogger("llmproxy.startup")


class StartupError(Exception):
    """Raised when a critical configuration issue prevents startup."""


# Captured during run_startup_checks() so the admin UI can surface them
# in the Settings → Config Warnings widget without re-running validation.
_LAST_WARNINGS: list[str] = []


# Actionable provider links for missing/invalid keys. Operators reading
# the warnings get a one-click path to the right place — billing for
# revoked/quota-exhausted keys, key dashboard for misconfigured ones.
_PROVIDER_LINKS = {
    "openai": "https://platform.openai.com/api-keys (billing: https://platform.openai.com/account/billing)",
    "anthropic": "https://console.anthropic.com/settings/keys (billing: https://console.anthropic.com/settings/billing)",
    "google": "https://aistudio.google.com/app/apikey",
    "azure": "https://portal.azure.com (Azure OpenAI resource → Keys and Endpoint)",
    "groq": "https://console.groq.com/keys",
    "mistral": "https://console.mistral.ai/api-keys/",
    "openrouter": "https://openrouter.ai/keys",
    "cohere": "https://dashboard.cohere.com/api-keys",
}


def _provider_link_hint(env_name: str, ep_provider: str) -> str:
    """Return a 'Get one at: <url>' suffix for a known provider, '' otherwise.

    Match by provider field first (config-declared), then by env-var prefix
    (covers OPENAI_API_KEY, ANTHROPIC_API_KEY, etc.).
    """
    key = (ep_provider or "").lower()
    if key not in _PROVIDER_LINKS:
        env_lower = env_name.lower()
        for known in _PROVIDER_LINKS:
            if known in env_lower:
                key = known
                break
    if key in _PROVIDER_LINKS:
        return f"\n  Get one at: {_PROVIDER_LINKS[key]}"
    return ""


def get_startup_warnings() -> list[str]:
    """Return a copy of the most recent run_startup_checks() warnings."""
    return list(_LAST_WARNINGS)


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

    # 2. At least one endpoint configured — soft requirement.
    #
    # Zero endpoints is a legitimate first-run state: the user runs the
    # installer, authenticates to the admin UI, and adds the first provider
    # through the onboarding wizard. We warn loudly but do NOT abort —
    # the UI and /health must stay reachable so the wizard can finish.
    endpoints = config.get("endpoints", {})
    if not endpoints:
        warnings.append(
            "No LLM endpoints configured — starting in ONBOARDING MODE.\n"
            "  Inference requests will 503 until at least one endpoint is added.\n"
            "  Open http://<host>:<port>/ui and use the onboarding wizard,\n"
            "  or set LLM_PROXY_ENDPOINT_<NAME>_URL=... in .env and restart."
        )

    # 3. Provider API keys — soft requirement, same rationale as above.
    active_providers = []
    for name, ep_cfg in endpoints.items():
        api_key_env = ep_cfg.get("api_key_env")
        auth_type = ep_cfg.get("auth_type", "bearer")

        if auth_type == "none":
            active_providers.append(name)
            continue

        if api_key_env:
            val = os.environ.get(api_key_env, "")
            # Check if the value is a real key (not a placeholder like "sk-proj-...")
            _PLACEHOLDERS = {
                "sk-proj-...",
                "sk-ant-...",
                "AIza...",
                "gsk_...",
                "your-api-key",
                "CHANGE-ME",
                "",
            }
            if val and val not in _PLACEHOLDERS:
                active_providers.append(name)
            elif not val:
                warnings.append(
                    f"Endpoint '{name}' needs {api_key_env} — skipped.\n"
                    f"  Set in .env: {api_key_env}=your-api-key"
                    + _provider_link_hint(api_key_env, ep_cfg.get("provider", ""))
                )

    if endpoints and not active_providers:
        warnings.append(
            "No endpoints have valid API keys yet — starting in ONBOARDING MODE.\n"
            "  Set a provider key in .env (e.g. OPENAI_API_KEY) and restart,\n"
            "  or complete setup from the admin UI."
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
        warnings.append(
            "security.max_payload_size_kb < 1 KB — most requests will be rejected"
        )

    # 6. Caching config
    cache_cfg = config.get("caching", {})
    if cache_cfg.get("enabled") and not cache_cfg.get("db_path"):
        warnings.append("Caching enabled but no db_path set — using 'cache.db'")

    return warnings


def run_startup_checks(config: dict):
    """Run all startup validations. Exits with clear message on failure."""
    global _LAST_WARNINGS
    try:
        warnings = validate_config(config)
        _LAST_WARNINGS = list(warnings)
        for w in warnings:
            logger.warning(f"CONFIG: {w}")
        if warnings:
            logger.info(f"Startup checks passed with {len(warnings)} warning(s)")
        else:
            logger.info("Startup checks passed")
    except StartupError as e:
        _LAST_WARNINGS = [str(e)]
        logger.critical(f"\n{'=' * 60}\n  STARTUP FAILED\n{'=' * 60}\n\n{e}\n")
        sys.exit(1)
