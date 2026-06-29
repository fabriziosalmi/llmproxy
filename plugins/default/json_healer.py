import json
import logging
from fastapi.responses import Response
from core.plugin_engine import PluginContext

logger = logging.getLogger("llmproxy.plugins.json_healer")


def _parse_depth(s: str) -> tuple[int, int, bool]:
    """Parse braces and brackets depth, ignoring strings and escaped chars."""
    in_string = False
    escaped = False
    braces_count = 0
    brackets_count = 0

    for char in s:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if not in_string:
            if char == "{":
                braces_count += 1
            elif char == "}":
                if braces_count > 0:
                    braces_count -= 1
            elif char == "[":
                brackets_count += 1
            elif char == "]":
                if brackets_count > 0:
                    brackets_count -= 1
    return braces_count, brackets_count, in_string


async def repair(ctx: PluginContext):
    """Ring 4: Post-Flight JSON Auto-Healer.

    Detects truncated JSON responses (e.g., from stream interruption)
    and attempts heuristic repair by closing unclosed brackets/braces.
    """
    rotator = ctx.metadata.get("rotator")
    if not ctx.response or not hasattr(ctx.response, "body"):
        return

    try:
        body_str = ctx.response.body.decode()
        # Does it look like truncated JSON?
        if body_str.strip().startswith("{") and not body_str.strip().endswith("}"):
            braces_count, brackets_count, in_string = _parse_depth(body_str)

            repaired = body_str.strip()
            if in_string:
                repaired += '"'

            repaired += "]" * brackets_count
            repaired += "}" * braces_count

            try:
                json.loads(repaired)
                # Response.body is read-only — create new Response
                ctx.response = Response(
                    content=repaired.encode(),
                    status_code=ctx.response.status_code,
                    media_type="application/json",
                )
                await rotator._add_log(
                    "JSON-HEALER: Repaired truncated response stream.", level="WARNING"
                )
            except Exception:
                logger.debug(
                    "JSON healer produced invalid repaired payload", exc_info=True
                )
    except Exception:
        logger.debug("JSON healer skipped due to unexpected error", exc_info=True)
