"""POST /v1/completions — Legacy text completion endpoint.

Translates legacy prompt-based requests to chat format, runs through the
full proxy pipeline, then translates the response back to legacy format.

Needed for: fine-tuned model deployments, LangChain OpenAI() (not ChatOpenAI()),
batch processing scripts, and any pre-2023 code.
"""

import json
import logging

from fastapi import APIRouter, Request, Depends
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader

logger = logging.getLogger("llmproxy.routes.completions")

API_KEY_HEADER = APIKeyHeader(name="Authorization", auto_error=False)


def create_router(agent) -> APIRouter:
    router = APIRouter()

    @router.post("/v1/completions")
    async def text_completions(request: Request, api_key: str = Depends(API_KEY_HEADER)):
        body = await request.json()

        # Translate legacy format → chat format
        prompt = body.pop("prompt", "")
        if isinstance(prompt, list):
            # Some clients send prompt as list of strings
            prompt = "\n".join(str(p) for p in prompt)

        chat_body = {
            **body,
            "messages": [{"role": "user", "content": prompt}],
        }

        # Session ID for security pipeline
        token = ""
        if api_key:
            token = api_key.replace("Bearer ", "").strip()
        session_id = token or (request.client.host if request.client else "anon")

        # Run through full proxy pipeline (auth, security, routing, forwarding)
        response = await agent.proxy_request(request, body=chat_body, session_id=session_id)

        # Translate chat response → legacy format
        if response and hasattr(response, "body"):
            try:
                data = json.loads(response.body.decode())
                choices = data.get("choices", [])
                legacy_choices = []
                for i, choice in enumerate(choices):
                    msg = choice.get("message", {})
                    legacy_choices.append({
                        "text": msg.get("content", ""),
                        "index": i,
                        "logprobs": None,
                        "finish_reason": choice.get("finish_reason", "stop"),
                    })

                legacy_data = {
                    "id": data.get("id", ""),
                    "object": "text_completion",
                    "created": data.get("created", 0),
                    "model": data.get("model", ""),
                    "choices": legacy_choices,
                    "usage": data.get("usage", {}),
                }
                return JSONResponse(content=legacy_data, status_code=response.status_code)
            except (json.JSONDecodeError, KeyError, IndexError):
                pass

        return response

    return router
