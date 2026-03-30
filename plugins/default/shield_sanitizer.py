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
        # H12: StreamingResponse has no .body — it's an async iterator.
        # Streaming responses are NOT sanitized by this plugin. The only
        # defense is the speculative guardrail (analyze_speculative) which
        # runs concurrently but is detect-only (bytes already sent).
        # To enforce full sanitization, disable streaming or use buffered mode.
        return

    try:
        data = json.loads(ctx.response.body.decode())
        choices = data.get("choices")
        if not choices:
            return

        # H3: Sanitize ALL choices, not just choices[0]. When n>1 is
        # requested, alternative completions bypass sanitization.
        any_blocked = False
        for choice in choices:
            raw_content = choice.get("message", {}).get("content", "")
            if not raw_content:
                continue
            sanitized = rotator.security.sanitize_response(raw_content)
            choice.setdefault("message", {})["content"] = sanitized
            if "[SEC_ERR:" in sanitized:
                any_blocked = True

        # JSONResponse.body is a read-only property — create a new Response
        new_body = json.dumps(data, separators=(",", ":")).encode()
        ctx.response = Response(
            content=new_body,
            status_code=ctx.response.status_code,
            media_type="application/json",
        )

        if any_blocked:
            ctx.error = "Security Shield blocked response."
            ctx.stop_chain = True

    except Exception as e:
        rotator.logger.error(f"Sanitization error: {e}")
