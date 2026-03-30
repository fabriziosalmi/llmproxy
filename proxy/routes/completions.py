"""POST /v1/completions — Legacy text completion endpoint.

Translates legacy prompt-based requests to chat format, runs through the
full proxy pipeline, then translates the response back to legacy format.

Handles both streaming and non-streaming responses:
  - Non-streaming: chat.completion → text_completion JSON
  - Streaming: chat.completion.chunk → text_completion SSE chunks

Needed for: fine-tuned model deployments, LangChain OpenAI() (not ChatOpenAI()),
batch processing scripts, and any pre-2023 code.
"""

import json
import logging

from fastapi import APIRouter, Request, Depends
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import APIKeyHeader

logger = logging.getLogger("llmproxy.routes.completions")

API_KEY_HEADER = APIKeyHeader(name="Authorization", auto_error=False)


def _translate_chat_chunk_to_legacy(chunk: bytes) -> bytes:
    """Translate a chat.completion.chunk SSE line to text_completion format."""
    text = chunk.decode("utf-8", errors="replace")
    output_lines = []

    for line in text.split("\n"):
        if not line.startswith("data: "):
            if line.strip():
                output_lines.append(line)
            continue

        data_str = line[6:].strip()
        if data_str == "[DONE]":
            output_lines.append("data: [DONE]")
            continue

        try:
            data = json.loads(data_str)
            delta = data.get("choices", [{}])[0].get("delta", {})
            content = delta.get("content", "")
            finish = data.get("choices", [{}])[0].get("finish_reason")

            legacy = {
                "id": data.get("id", ""),
                "object": "text_completion",
                "created": data.get("created", 0),
                "model": data.get("model", ""),
                "choices": [{
                    "text": content,
                    "index": 0,
                    "logprobs": None,
                    "finish_reason": finish,
                }],
            }
            output_lines.append(f"data: {json.dumps(legacy)}")
        except (json.JSONDecodeError, KeyError, IndexError):
            output_lines.append(line)

    result = "\n".join(output_lines)
    if result:
        result += "\n\n"
    return result.encode("utf-8")


def create_router(agent) -> APIRouter:
    router = APIRouter()

    @router.post("/v1/completions")
    async def text_completions(request: Request, api_key: str = Depends(API_KEY_HEADER)):
        body = await request.json()

        # Translate legacy format → chat format
        prompt = body.pop("prompt", "")
        if isinstance(prompt, list):
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

        # Run through full proxy pipeline
        response = await agent.proxy_request(request, body=chat_body, session_id=session_id)

        # Streaming: translate each SSE chunk from chat to legacy format
        if isinstance(response, StreamingResponse):
            original_body_iterator = response.body_iterator

            async def legacy_stream():
                async for chunk in original_body_iterator:
                    if isinstance(chunk, str):
                        chunk = chunk.encode("utf-8")
                    translated = _translate_chat_chunk_to_legacy(chunk)
                    if translated:
                        yield translated

            return StreamingResponse(legacy_stream(), media_type="text/event-stream")

        # Non-streaming: translate full response
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
