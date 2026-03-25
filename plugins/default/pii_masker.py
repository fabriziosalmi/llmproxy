from core.plugin_engine import PluginContext

async def mask(ctx: PluginContext):
    """Ring 2: Pre-Flight PII Neural Masking."""
    rotator = ctx.metadata.get("rotator")
    body = ctx.body

    messages = body.get("messages")
    if not messages:
        return

    prompt = messages[-1].get("content", "")
    masked_prompt = rotator.security.mask_pii(prompt)

    if masked_prompt != prompt:
        messages[-1]["content"] = masked_prompt
        ctx.metadata["pii_masked"] = True
        await rotator._add_log("SHIELD: Neural PII Masking applied to prompt", level="SYSTEM")
