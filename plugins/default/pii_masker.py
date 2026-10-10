from core.plugin_engine import PluginContext


async def mask(ctx: PluginContext):
    """Ring 2: Pre-Flight PII masking.

    H2: Masks PII in ALL messages, not just the last. An attacker can
    hide PII (SSN, credit card) in earlier messages which are forwarded
    to the upstream LLM provider in cleartext.
    """
    rotator = ctx.require_rotator()
    body = ctx.body

    messages = body.get("messages")
    if not messages:
        return

    # Per-request vault. The shield's own vault is process-wide, so a token
    # minted here for one caller used to be resolvable in another caller's
    # response — demask_pii walked every live entry against every response.
    # Keeping the mapping on ctx.metadata scopes it to this request, and
    # shield_sanitizer reads the same dict back on the post-flight ring.
    vault = ctx.metadata.setdefault("_pii_vault", {})

    def _mask(text):
        if not text or not isinstance(text, str):
            return text, False
        masked = rotator.security.mask_pii(text, vault=vault)
        return masked, masked != text

    any_masked = False
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if isinstance(content, str):
            msg["content"], changed = _mask(content)
            any_masked = any_masked or changed
        elif isinstance(content, list):
            # Content parts: what every vision client sends, and what any client
            # may send. Only plain strings were masked, so the same sentence
            # went upstream untouched once it was wrapped in a text part.
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    part["text"], changed = _mask(part["text"])
                    any_masked = any_masked or changed

    if any_masked:
        ctx.metadata["pii_masked"] = True
        await rotator._add_log(
            "SHIELD: PII masking applied to messages", level="SYSTEM"
        )
