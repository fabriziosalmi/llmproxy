import os
import base64
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from typing import Optional

class SecretManager:
    """Handles encryption/decryption of sensitive keys at rest."""
    
    _fernet = None

    @classmethod
    def _get_fernet(cls) -> Fernet:
        if cls._fernet:
            return cls._fernet
        
        # Use MASTER_KEY from env, fallback to a derived key if not found (NOT RECOMMENDED for prod)
        master_key = os.environ.get("LLM_PROXY_MASTER_KEY", "llm-proxy-default-unsafe-key-123")
        salt = b'llmproxy_salt_2026' # Should be unique/stored, but keeping it simple for now
        
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
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
        """Decrypts a string."""
        if not encrypted_secret:
            return ""
        try:
            return cls._get_fernet().decrypt(encrypted_secret.encode()).decode()
        except Exception as e:
            # Fallback: if decryption fails, maybe it's not encrypted (for migration phase)
            return encrypted_secret

    @classmethod
    def get_secret(cls, key_name: str, default: Optional[str] = None) -> Optional[str]:
        """Retrieves a secret from environment variables or encrypted storage (stub)."""
        # Env vars always take priority
        val = os.environ.get(key_name)
        if val:
            return val
        return default
