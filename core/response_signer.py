"""
Response Signer — Cryptographic provenance for LLM responses.

Signs each response with HMAC-SHA256 to prove it originated from
this proxy. The signature covers: model + provider + timestamp + response_hash.

Verification: any client with the shared secret can verify the signature
matches the response, proving it wasn't tampered with in transit.

Config:
  security:
    response_signing:
      enabled: true
      # Secret: from env var LLM_PROXY_SIGNING_KEY, or config (env preferred)
      # If not set, signing is disabled with a warning.

Headers injected:
  X-LLMProxy-Signature: HMAC-SHA256 hex digest
  X-LLMProxy-Signed-At: ISO 8601 timestamp
  X-LLMProxy-Signed-Fields: comma-separated list of signed fields
"""

import hmac
import hashlib
import time
import logging
logger = logging.getLogger("llmproxy.response_signer")


class ResponseSigner:
    """Signs LLM responses with HMAC-SHA256 for provenance verification."""

    def __init__(self, secret: str = ""):
        if secret:
            self._key = secret.encode("utf-8")
            self.enabled = True
        else:
            self._key = b""
            self.enabled = False
            logger.info("Response signing disabled (no signing key configured)")

    def sign_response(
        self,
        response_body: bytes,
        model: str = "",
        provider: str = "",
        request_id: str = "",
    ) -> dict[str, str]:
        """Compute HMAC-SHA256 signature over response + metadata.

        Returns dict of headers to inject into the response.
        Returns empty dict if signing is disabled.
        """
        if not self.enabled:
            return {}

        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        # Build the message to sign: deterministic concatenation
        # Using | as separator (not present in base64/hex)
        message = f"{model}|{provider}|{timestamp}|{request_id}|".encode("utf-8")
        message += response_body

        sig = hmac.new(self._key, message, hashlib.sha256).hexdigest()

        return {
            "X-LLMProxy-Signature": sig,
            "X-LLMProxy-Signed-At": timestamp,
            "X-LLMProxy-Signed-Fields": "model,provider,timestamp,request_id,body",
        }

    @staticmethod
    def verify(
        secret: str,
        response_body: bytes,
        model: str,
        provider: str,
        timestamp: str,
        request_id: str,
        expected_signature: str,
    ) -> bool:
        """Verify a response signature (client-side or audit tool).

        Args:
            secret: Shared signing key
            response_body: Raw response bytes
            model, provider, timestamp, request_id: Signed metadata fields
            expected_signature: The X-LLMProxy-Signature header value

        Returns True if signature matches, False otherwise.
        """
        message = f"{model}|{provider}|{timestamp}|{request_id}|".encode("utf-8")
        message += response_body

        computed = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
        return hmac.compare_digest(computed, expected_signature)
