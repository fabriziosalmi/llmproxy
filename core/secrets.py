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
    """Secret resolution, and encryption primitives that nothing currently uses.

    `get_secret` is the live surface: proxy/auth_helpers.py resolves both key
    bags through it on every request.

    `encrypt` and `decrypt` are NOT called anywhere in this codebase. The
    design does not need them — provider credentials are referenced by the
    NAME of an environment variable and never written to disk, which is a
    stronger property than encrypting them would be — so the Fernet key, the
    PBKDF2 salt and LLM_PROXY_MASTER_KEY protect no stored data today, and no
    salt file is created on a fresh install because nothing asks for a Fernet.

    They are kept rather than deleted because the primitives are correct and
    the natural future caller is visible (core/rbac.py's quotas table is the
    only place a client credential would ever be persisted). What was removed
    is the documentation claiming this is already protecting `data/`, and
    decrypt's fail-open. tests/test_encryption_is_not_wired.py records the
    current state so that wiring it up is a deliberate, visible change.
    """

    _fernet = None
    _salt = None

    #: PBKDF2 salt length. Fixed — a file of any other size is corrupt.
    _SALT_BYTES = 32

    #: Where the salt lives by default: beside the database, inside the
    #: directory operators already mount as a volume.
    _DEFAULT_SALT_PATH = "data/.llmproxy_salt"

    #: Where it used to live — the process working directory, which in a
    #: container is image content rather than volume.
    _LEGACY_SALT_PATH = ".llmproxy_salt"

    @classmethod
    def _resolve_salt_path(cls) -> str:
        """Locate the PBKDF2 salt, preferring an existing file over the default.

        The salt is one half of the key that decrypts every stored credential;
        the database holding that ciphertext is the other half. They used to be
        placed in different durability classes — docker-compose mounts a volume
        at /app/data and the salt defaulted to /app/.llmproxy_salt, so a rebuild
        preserved the ciphertext and discarded the key. The default is now
        data/, which is the directory the deployment guide already calls the
        only state that cannot be reconstructed.

        Resolution order matters more than the default: an existing legacy file
        wins, because silently switching an upgraded deployment to a new path
        would generate a fresh salt and orphan everything encrypted under the
        old one — the exact loss this change exists to prevent.
        """
        configured = os.environ.get("LLM_PROXY_SALT_PATH")
        if configured:
            return configured

        if not os.path.exists(cls._DEFAULT_SALT_PATH) and os.path.exists(
            cls._LEGACY_SALT_PATH
        ):
            logger.warning(
                "Encryption salt found at '%s', outside the data directory. "
                "A container rebuild or redeploy will discard it while keeping "
                "the encrypted database, making every stored secret unreadable. "
                "Move it to '%s' (or set LLM_PROXY_SALT_PATH) while the proxy "
                "is stopped.",
                cls._LEGACY_SALT_PATH,
                cls._DEFAULT_SALT_PATH,
            )
            return cls._LEGACY_SALT_PATH

        return cls._DEFAULT_SALT_PATH

    @classmethod
    def _get_fernet(cls) -> Fernet:
        if cls._fernet:
            return cls._fernet

        master_key = get_secret("LLM_PROXY_MASTER_KEY", required=True)
        if master_key is None:
            raise ValueError("LLM_PROXY_MASTER_KEY is required but not set")

        salt_path = cls._resolve_salt_path()
        if os.path.exists(salt_path):
            with open(salt_path, "rb") as f:
                salt = f.read()
            # A short read here is unrecoverable and silent: a different salt
            # derives a different key, every stored value fails to decrypt, and
            # decrypt() returns the ciphertext as if it were the plaintext — so
            # the proxy carries on with Fernet tokens where credentials should
            # be and the symptom is 401s from every provider. Refuse to start
            # instead, naming the file.
            if len(salt) != cls._SALT_BYTES:
                raise ValueError(
                    f"Salt file '{salt_path}' is {len(salt)} bytes, expected "
                    f"{cls._SALT_BYTES}. It is truncated or corrupt. Every value "
                    "encrypted under the original salt is unreadable without it; "
                    "restore that file from backup rather than deleting it."
                )
        else:
            parent = os.path.dirname(salt_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            salt = stdlib_secrets.token_bytes(cls._SALT_BYTES)
            fd = os.open(salt_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.write(fd, salt)
            finally:
                os.close(fd)
            logger.info("Generated a new encryption salt at %s", salt_path)

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
        """Decrypt a string, or raise.

        This used to return its INPUT when decryption failed, logged at ERROR,
        as a migration shim for values written before encryption existed. The
        docstring said it "MUST be removed once all secrets are encrypted" and
        it outlived that by several releases.

        Returning the ciphertext as if it were plaintext is the worst available
        outcome: a wrong master key or a mismatched salt would hand Fernet
        tokens to callers as credentials, the forwarder would send them
        upstream, and the symptom would be 401s from every provider with
        nothing naming decryption as the cause. That is exactly the failure the
        salt-length check added in 1.34.0 exists to prevent, arriving by a
        different route. A value that fails to decrypt is not a secret.
        """
        if not encrypted_secret:
            return ""
        try:
            return cls._get_fernet().decrypt(encrypted_secret.encode()).decode()
        except InvalidToken as e:
            raise ValueError(
                "Stored value could not be decrypted: it was encrypted with a "
                "different LLM_PROXY_MASTER_KEY or a different salt, or it was "
                "never encrypted at all. Restore the original salt file rather "
                "than deleting it — every value encrypted under it is "
                "unreadable without it."
            ) from e
        except (ValueError, TypeError) as e:
            # ValueError covers binascii.Error (non-base64 input); TypeError
            # covers None or a wrong type reaching here.
            raise ValueError(
                f"Stored value is not a valid encrypted token ({type(e).__name__})."
            ) from e

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
