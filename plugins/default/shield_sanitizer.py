"""
Shield Sanitizer — Ring 4: Post-Flight Response Sanitization

Applies SecurityShield.sanitize_response() to the LLM response content.
Detects language anomalies, response injections, steganography, and
re-maps masked PII tokens back to their original values.

Note: JSONResponse.body is a read-only property — a new Response object
is constructed rather than mutating the existing one.
"""

import json
from fastapi.responses import Response
from core.plugin_engine import PluginContext


async def cleanse(ctx: PluginContext):
    """Ring 4: Post-Flight Sanitization & Watermarking."""
    rotator = ctx.metadata.get("rotator")
    if not ctx.response or not hasattr(ctx.response, "body"):
        return

    try:
        data = json.loads(ctx.response.body.decode())
        choices = data.get("choices")
        if not choices:
            return

        raw_content = choices[0].get("message", {}).get("content", "")
        if not raw_content:
            return

        sanitized = rotator.security.sanitize_response(raw_content)
        choices[0].setdefault("message", {})["content"] = sanitized

        # JSONResponse.body is a read-only property — create a new Response
        new_body = json.dumps(data, separators=(",", ":")).encode()
        ctx.response = Response(
            content=new_body,
            status_code=ctx.response.status_code,
            media_type="application/json",
        )

        if "[SEC_ERR:" in sanitized:
            ctx.error = "Security Shield blocked response."
            ctx.stop_chain = True

    except Exception as e:
        rotator.logger.error(f"Sanitization error: {e}")
