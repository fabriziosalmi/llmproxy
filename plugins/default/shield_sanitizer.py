from core.plugin_engine import PluginContext
import json

async def cleanse(ctx: PluginContext):
    """Ring 4: Post-Flight Sanitization & Watermarking."""
    rotator = ctx.metadata.get("rotator")
    if not ctx.response or not hasattr(ctx.response, 'body'):
        return

    # For blocking responses, we can sanitize the full text
    try:
        data = json.loads(ctx.response.body.decode())
        if "choices" in data:
            raw_content = data['choices'][0]['message'].get('content', "")
            if raw_content:
                sanitized = rotator.security.sanitize_response(raw_content)
                data['choices'][0]['message']['content'] = sanitized
                ctx.response.body = json.dumps(data).encode()
                
                # Check for major anomalies
                if "[SEC_ERR:" in sanitized:
                    ctx.error = "Security Shield blocked response."
                    ctx.stop_chain = True
    except Exception as e:
        rotator.logger.error(f"Sanitization error: {e}")
