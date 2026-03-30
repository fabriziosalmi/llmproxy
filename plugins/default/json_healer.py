import json
from core.plugin_engine import PluginContext

async def repair(ctx: PluginContext):
    """Ring 4: Post-Flight JSON Auto-Healer."""
    rotator = ctx.metadata.get("rotator")
    if not ctx.response or not hasattr(ctx.response, 'body'):
        return

    try:
        body_str = ctx.response.body.decode()
        # Does it look like truncated JSON?
        if body_str.strip().startswith('{') and not body_str.strip().endswith('}'):
            # Simple heuristic repair
            open_braces = body_str.count('{')
            close_braces = body_str.count('}')
            open_brackets = body_str.count('[')
            close_brackets = body_str.count(']')

            repaired = body_str.strip()
            # Close strings if needed
            if repaired.count('"') % 2 != 0:
                repaired += '"'

            # Close arrays/objects
            repaired += ']' * (open_brackets - close_brackets)
            repaired += '}' * (open_braces - close_braces)

            # Verify if it's now parseable
            try:
                json.loads(repaired)
                ctx.response.body = repaired.encode()
                await rotator._add_log("JSON-HEALER: Repaired truncated response stream.", level="WARNING")
            except Exception:
                pass
    except Exception:
        pass
