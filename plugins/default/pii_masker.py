from core.plugin_engine import PluginContext

async def mask(ctx: PluginContext):
    """Ring 2: Pre-Flight PII Neural Masking.

    H2: Masks PII in ALL messages, not just the last. An attacker can
    hide PII (SSN, credit card) in earlier messages which are forwarded
    to the upstream LLM provider in cleartext.
    """
    rotator = ctx.metadata.get("rotator")
    body = ctx.body

    messages = body.get("messages")
    if not messages:
        return

    any_masked = False
    for msg in messages:
        content = msg.get("content", "")
        if not content or not isinstance(content, str):
            continue
        masked = rotator.security.mask_pii(content)
        if masked != content:
            msg["content"] = masked
            any_masked = True

    if any_masked:
        ctx.metadata["pii_masked"] = True
        await rotator._add_log("SHIELD: Neural PII Masking applied to messages", level="SYSTEM")
