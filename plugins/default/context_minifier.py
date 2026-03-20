import re
from core.plugin_engine import PluginContext

async def compress(ctx: PluginContext):
    """Ring 2: Pre-Flight Context Minification."""
    body = ctx.body
    if not body.get("messages"):
        return

    rotator = ctx.metadata.get("rotator")
    modified = False
    
    # Process the last message (usually the prompt containing file context)
    last_msg = body["messages"][-1]
    content = last_msg.get("content", "")
    
    if len(content) > 1000: # Only minify large payloads
        # 1. Remove multi-line comments (/* ... */)
        content = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)
        # 2. Remove single-line comments (// ...)
        content = re.sub(r'//.*?\n', '\n', content)
        # 3. Collapse multiple newlines/spaces
        content = re.sub(r'\n\s*\n', '\n', content)
        content = re.sub(r'[ \t]{2,}', ' ', content)
        
        last_msg["content"] = content
        modified = True

    if modified:
        ctx.metadata["minified"] = True
        await rotator._add_log(f"MINIFIER: Reduced prompt size by {len(content)} chars.", level="SYSTEM")
