import re
from core.plugin_engine import PluginContext


async def compress(ctx: PluginContext):
    """Ring 2: Pre-Flight Context Minification."""
    body = ctx.body
    messages = body.get("messages")
    if not messages:
        return

    rotator = ctx.metadata.get("rotator")
    modified = False

    # Process the last message (usually the prompt containing file context)
    last_msg = messages[-1]
    content = last_msg.get("content")
    if not content:
        return

    original_len = 0
    reduction = 0

    if isinstance(content, str):
        original_len = len(content)
        if original_len > 1000:  # Only minify large payloads
            content = re.sub(r"/\*.*?\*/", "", content, flags=re.DOTALL)
            content = re.sub(r"(?<!:)//.*?\n", "\n", content)
            content = re.sub(r"\n\s*\n", "\n", content)
            content = re.sub(r"[ \t]{2,}", " ", content)

            last_msg["content"] = content
            modified = True
            reduction = original_len - len(content)
            final_len = len(content)
    elif isinstance(content, list):
        # Support for multimodal content lists containing text blocks
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                text_val = block["text"]
                block_len = len(text_val)
                original_len += block_len
                if block_len > 1000:
                    text_val = re.sub(r"/\*.*?\*/", "", text_val, flags=re.DOTALL)
                    text_val = re.sub(r"(?<!:)//.*?\n", "\n", text_val)
                    text_val = re.sub(r"\n\s*\n", "\n", text_val)
                    text_val = re.sub(r"[ \t]{2,}", " ", text_val)
                    block["text"] = text_val
                    reduction += block_len - len(text_val)
                    modified = True
        final_len = original_len - reduction

    if modified:
        ctx.metadata["minified"] = True
        await rotator._add_log(
            f"MINIFIER: Reduced prompt by {reduction} chars ({original_len} → {final_len}).",
            level="SYSTEM",
        )
