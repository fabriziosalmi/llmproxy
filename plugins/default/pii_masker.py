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

    # Per-request vault. The shield's own vault is process-wide, so a token
    # minted here for one caller used to be resolvable in another caller's
    # response — demask_pii walked every live entry against every response.
    # Keeping the mapping on ctx.metadata scopes it to this request, and
    # shield_sanitizer reads the same dict back on the post-flight ring.
    vault = ctx.metadata.setdefault("_pii_vault", {})

    any_masked = False
    for msg in messages:
        content = msg.get("content", "")
        if not content or not isinstance(content, str):
            continue
        masked = rotator.security.mask_pii(content, vault=vault)
        if masked != content:
            msg["content"] = masked
            any_masked = True

    if any_masked:
        ctx.metadata["pii_masked"] = True
        await rotator._add_log(
            "SHIELD: Neural PII Masking applied to messages", level="SYSTEM"
        )
