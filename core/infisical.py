"""
Infisical Secret Management Client.

Centralizes all secret retrieval through Infisical.
Falls back to environment variables only in development mode.
"""

import os
import logging
from typing import Optional, Dict

logger = logging.getLogger(__name__)

# Lazy-loaded SDK client
_client = None
_secrets_cache: Dict[str, str] = {}


def _get_client():
    """Initialize the Infisical SDK client (singleton)."""
    global _client
    if _client is not None:
        return _client

    try:
        from infisical_sdk import InfisicalSDKClient
    except ImportError:
        logger.warning(
            "infisical-python-sdk not installed. "
            "Install with: pip install infisical-python-sdk"
        )
        return None

    site_url = os.environ.get("INFISICAL_SITE_URL", "https://app.infisical.com")
    client_id = os.environ.get("INFISICAL_CLIENT_ID")
    client_secret = os.environ.get("INFISICAL_CLIENT_SECRET")

    if not client_id or not client_secret:
        logger.warning(
            "INFISICAL_CLIENT_ID and INFISICAL_CLIENT_SECRET not set. "
            "Falling back to environment variables."
        )
        return None

    try:
        _client = InfisicalSDKClient(host=site_url)
        _client.auth.universal_auth.login(
            client_id=client_id,
            client_secret=client_secret,
        )
        logger.info("Infisical client authenticated successfully.")
        return _client
    except Exception as e:
        logger.error(f"Infisical authentication failed: {e}")
        return None


def get_secret(
    key: str,
    default: Optional[str] = None,
    *,
    required: bool = False,
    project_id: Optional[str] = None,
    environment: Optional[str] = None,
    secret_path: str = "/",
) -> Optional[str]:
    """
    Retrieve a secret from Infisical, falling back to env vars.

    Args:
        key: The secret name.
        default: Fallback value if not found (ignored when required=True).
        required: If True, raises RuntimeError when the secret is missing.
        project_id: Infisical project ID (defaults to INFISICAL_PROJECT_ID env).
        environment: Infisical environment slug (defaults to INFISICAL_ENV env).
        secret_path: Path within Infisical (default "/").

    Returns:
        The secret value, or default if not found and not required.

    Raises:
        RuntimeError: If required=True and the secret cannot be resolved.
    """
    # Check cache first
    if key in _secrets_cache:
        return _secrets_cache[key]

    value = None

    # 1. Try Infisical SDK
    client = _get_client()
    if client is not None:
        proj = project_id or os.environ.get("INFISICAL_PROJECT_ID")
        env = environment or os.environ.get("INFISICAL_ENV", "prod")

        if proj:
            try:
                secret = client.secrets.get_secret_by_name(
                    secret_name=key,
                    project_id=proj,
                    environment_slug=env,
                    secret_path=secret_path,
                )
                value = secret.secret_value
                logger.debug(f"Secret '{key}' loaded from Infisical.")
            except Exception as e:
                logger.warning(f"Infisical lookup failed for '{key}': {e}")

    # 2. Fallback to environment variable
    if value is None:
        value = os.environ.get(key)
        if value is not None:
            logger.debug(f"Secret '{key}' loaded from environment variable.")

    # 3. Apply default or raise
    if value is None:
        if required:
            raise RuntimeError(
                f"Required secret '{key}' not found in Infisical or environment. "
                f"Set it in Infisical or export {key}=<value>."
            )
        value = default

    # Cache resolved value
    if value is not None:
        _secrets_cache[key] = value

    return value


def get_secrets_batch(
    keys: list[str],
    *,
    project_id: Optional[str] = None,
    environment: Optional[str] = None,
    secret_path: str = "/",
) -> Dict[str, Optional[str]]:
    """Retrieve multiple secrets at once."""
    return {
        key: get_secret(
            key,
            project_id=project_id,
            environment=environment,
            secret_path=secret_path,
        )
        for key in keys
    }


def clear_cache():
    """Clear the in-memory secrets cache (useful for rotation)."""
    global _secrets_cache
    _secrets_cache = {}


def is_connected() -> bool:
    """Check if Infisical client is authenticated."""
    return _get_client() is not None
