import json
from fastapi.responses import Response
from core.plugin_engine import PluginContext

async def repair(ctx: PluginContext):
    """Ring 4: Post-Flight JSON Auto-Healer.

    Detects truncated JSON responses (e.g., from stream interruption)
    and attempts heuristic repair by closing unclosed brackets/braces.
    """
    rotator = ctx.metadata.get("rotator")
    if not ctx.response or not hasattr(ctx.response, 'body'):
        return

    try:
        body_str = ctx.response.body.decode()
        # Does it look like truncated JSON?
        if body_str.strip().startswith('{') and not body_str.strip().endswith('}'):
            open_braces = body_str.count('{')
            close_braces = body_str.count('}')
            open_brackets = body_str.count('[')
            close_brackets = body_str.count(']')

            repaired = body_str.strip()
            if repaired.count('"') % 2 != 0:
                repaired += '"'

            repaired += ']' * (open_brackets - close_brackets)
            repaired += '}' * (open_braces - close_braces)

            try:
                json.loads(repaired)
                # Response.body is read-only — create new Response
                ctx.response = Response(
                    content=repaired.encode(),
                    status_code=ctx.response.status_code,
                    media_type="application/json",
                )
                await rotator._add_log("JSON-HEALER: Repaired truncated response stream.", level="WARNING")
            except Exception:
                pass
    except Exception:
        pass
