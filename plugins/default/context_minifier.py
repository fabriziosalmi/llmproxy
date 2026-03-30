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
    content = last_msg.get("content", "")

    original_len = len(content)
    if original_len > 1000:  # Only minify large payloads
        content = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)
        content = re.sub(r'//.*?\n', '\n', content)
        content = re.sub(r'\n\s*\n', '\n', content)
        content = re.sub(r'[ \t]{2,}', ' ', content)

        last_msg["content"] = content
        modified = True

    if modified:
        reduction = original_len - len(content)
        ctx.metadata["minified"] = True
        await rotator._add_log(f"MINIFIER: Reduced prompt by {reduction} chars ({original_len} → {len(content)}).", level="SYSTEM")
