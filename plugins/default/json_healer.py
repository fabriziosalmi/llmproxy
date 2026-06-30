import json
import logging
from fastapi.responses import Response
from core.plugin_engine import PluginContext

logger = logging.getLogger("llmproxy.plugins.json_healer")


def _get_closing_sequence(s: str) -> tuple[str, str, bool]:
    """Parse braces and brackets stack to find closing tokens.

    Returns:
        1. Adjusted string (with trailing commas stripped or colons completed).
        2. Sequence of closing tokens to balance the nesting.
        3. Whether the cursor is currently inside a string.
    """
    in_string = False
    escaped = False
    stack = []

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
                stack.append("}")
            elif char == "[":
                stack.append("]")
            elif char == "}":
                if stack and stack[-1] == "}":
                    stack.pop()
                elif "}" in stack:
                    for i in range(len(stack) - 1, -1, -1):
                        if stack[i] == "}":
                            stack.pop(i)
                            break
            elif char == "]":
                if stack and stack[-1] == "]":
                    stack.pop()
                elif "]" in stack:
                    for i in range(len(stack) - 1, -1, -1):
                        if stack[i] == "]":
                            stack.pop(i)
                            break

    adjusted_s = s.rstrip()
    if not in_string:
        if adjusted_s.endswith(","):
            adjusted_s = adjusted_s[:-1].rstrip()
        elif adjusted_s.endswith(":"):
            adjusted_s += "null"

    closing = "".join(reversed(stack))
    return adjusted_s, closing, in_string


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
        stripped = body_str.strip()
        # Does it look like truncated JSON?
        if (stripped.startswith("{") or stripped.startswith("[")) and not (
            stripped.endswith("}") or stripped.endswith("]")
        ):
            adjusted_s, closing, in_string = _get_closing_sequence(body_str)

            repaired = adjusted_s
            if in_string:
                repaired += '"'
            repaired += closing

            try:
                json.loads(repaired)
                # Copy and preserve response headers (excluding content-length)
                headers = dict(ctx.response.headers)
                headers.pop("content-length", None)

                # Response.body is read-only — create new Response
                ctx.response = Response(
                    content=repaired.encode(),
                    status_code=ctx.response.status_code,
                    headers=headers,
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
