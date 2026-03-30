import re
from core.plugin_engine import PluginContext

async def analyze(ctx: PluginContext):
    """Ring 4: Post-Flight Speculative Kill-Switch."""
    rotator = ctx.metadata.get("rotator")
    if not ctx.response or not hasattr(ctx.response, 'body'):
        return

    try:
        data = ctx.response.body.decode()

        # 1. Detect simple infinite loops (repetitive characters/words)
        # Look for the same word repeating more than 15 times in a row
        if re.search(r'(\b\w+\b)( \1){15,}', data):
            # Loop detected! Snip the body.
            ctx.response.body = b" [LLMPROXY_ERROR: INFINITE_LOOP_DETECTED_SNIPPED]"
            await rotator._add_log("KILL-SWITCH: Infinite loop detected. Snipping response stream.", level="CRITICAL")
            return

        # 2. Detect "empty babbling" (e.g. infinite newlines)
        if data.count('\n\n\n\n\n\n\n\n\n\n') > 2:
            ctx.response.body = b" [LLMPROXY_ERROR: STUTTERING_DETECTED_SNIPPED]"
            await rotator._add_log("KILL-SWITCH: Model stuttering (excessive newlines). Snipping.", level="CRITICAL")

    except Exception:
        pass
