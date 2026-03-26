import os
import base64
import logging
import secrets as stdlib_secrets
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from typing import Optional

from core.infisical import get_secret

logger = logging.getLogger(__name__)


class SecretManager:
    """Handles encryption/decryption of sensitive keys at rest via Infisical."""

    _fernet = None
    _salt = None

    @classmethod
    def _get_fernet(cls) -> Fernet:
        if cls._fernet:
            return cls._fernet

        master_key = get_secret("LLM_PROXY_MASTER_KEY", required=True)
        if master_key is None:
            raise ValueError("LLM_PROXY_MASTER_KEY is required but not set")

        # Per-instance salt stored alongside the app data.
        # Generated once, persisted to disk so existing encrypted values remain decryptable.
        salt_path = os.environ.get("LLM_PROXY_SALT_PATH", ".llmproxy_salt")
        if os.path.exists(salt_path):
            with open(salt_path, "rb") as f:
                salt = f.read()
        else:
            salt = stdlib_secrets.token_bytes(32)
            fd = os.open(salt_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.write(fd, salt)
            finally:
                os.close(fd)

        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=600_000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(master_key.encode()))
        cls._fernet = Fernet(key)
        return cls._fernet

    @classmethod
    def encrypt(cls, secret: str) -> str:
        """Encrypts a string."""
        if not secret:
            return ""
        return cls._get_fernet().encrypt(secret.encode()).decode()

    @classmethod
    def decrypt(cls, encrypted_secret: str) -> str:
        """Decrypts a string.

        Migration path: if decryption fails because the stored value is still
        plaintext (pre-encryption migration), the raw value is returned and an
        ERROR is logged.  This fallback exists only to smooth key-rotation
        migrations; it MUST be removed once all secrets are encrypted.

        Security note: returning plaintext on decryption failure is an
        intentional, logged degradation — not a silent bypass.  Any unexpected
        exception (not related to a wrong/missing key) is re-raised so that
        injection of malformed data cannot silently pass as a valid secret.
        """
        if not encrypted_secret:
            return ""
        try:
            return cls._get_fernet().decrypt(encrypted_secret.encode()).decode()
        except InvalidToken:
            # Expected during migration: value is plaintext or was encrypted
            # with a different key.  Log at ERROR — this should be rare/transient.
            logger.error(
                "Decryption failed (InvalidToken) — returning raw value as migration fallback. "
                "Ensure all secrets are re-encrypted if this persists."
            )
            return encrypted_secret
        except (ValueError, TypeError) as e:
            # ValueError covers binascii.Error (non-base64 plaintext input).
            # TypeError covers None/wrong-type inputs that slipped through.
            # Both indicate the stored value was never encrypted; treat as
            # migration fallback the same as InvalidToken.
            logger.error(
                f"Decryption failed ({type(e).__name__}) — returning raw value as migration fallback. "
                "Ensure all secrets are re-encrypted if this persists."
            )
            return encrypted_secret

    @classmethod
    def get_secret(
        cls,
        key_name: str,
        default: Optional[str] = None,
        *,
        required: bool = False,
    ) -> Optional[str]:
        """Retrieves a secret from Infisical, then env vars."""
        return get_secret(key_name, default, required=required)
