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
import hashlib
import time
import datetime as _dt

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import APIKeyHeader

from core.metrics import MetricsTracker
from core.pricing import estimate_cost

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
                "choices": [
                    {
                        "text": content,
                        "index": 0,
                        "logprobs": None,
                        "finish_reason": finish,
                    }
                ],
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
    async def text_completions(
        request: Request, api_key: str = Depends(API_KEY_HEADER)
    ):
        # Auth parity with /v1/chat/completions.
        token = ""
        if agent.config["server"]["auth"]["enabled"]:
            if not api_key:
                raise HTTPException(
                    status_code=401, detail="Unauthorized: Missing API key"
                )
            token = api_key.replace("Bearer ", "").strip()
            if not token:
                raise HTTPException(status_code=401, detail="Unauthorized: Empty token")

            identity = None
            if agent.identity.enabled:
                try:
                    identity = agent.identity.verify_proxy_jwt(token)
                    if not identity:
                        identity = await agent.identity.verify_token(token)
                except ValueError:
                    raise HTTPException(
                        status_code=401, detail="Unauthorized: Invalid or expired token"
                    )

            if identity and identity.verified:
                request.state.identity = identity
                request.state.user = identity.email or identity.subject
                request.state.roles = identity.roles
                if not agent.rbac.check_permission(identity.roles, "proxy:use"):
                    raise HTTPException(
                        status_code=403, detail="Insufficient permissions"
                    )
                await agent.rbac.set_user_roles(
                    identity.subject, identity.email, identity.roles
                )
            else:
                if not agent._verify_api_key(token):
                    raise HTTPException(
                        status_code=401, detail="Unauthorized: Invalid API key or JWT"
                    )

                if not await agent.rbac.check_quota(token):
                    request.state.quota_exceeded = True

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
        if token:
            session_id = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
        else:
            ip = request.client.host if request.client else "anon"
            ua = request.headers.get("user-agent", "")
            lang = request.headers.get("accept-language", "")
            session_id = hashlib.sha256(
                f"{ip}:{ua}:{lang}".encode("utf-8")
            ).hexdigest()[:16]

        # Run through full proxy pipeline
        _start = time.time()
        response = await agent.proxy_request(
            request, body=chat_body, session_id=session_id
        )
        _duration = time.time() - _start

        # Audit + spend persistence for NON-streaming legacy completions.
        # Streaming requests are logged by forwarder._handle_streaming (single
        # chokepoint). chat.py has its own block; this is parity for the legacy
        # path which otherwise leaves the audit ledger silent (live walkthrough
        # 2026-05-20: 12 completions, 0 audit entries — root cause was this gap).
        if (
            not isinstance(response, StreamingResponse)
            and response is not None
            and hasattr(response, "body")
        ):
            try:
                _now = int(time.time())
                _date = _dt.date.today().isoformat()
                _key = (token[:8] + "...") if token else ""
                _provider = ""
                _req_id = ""
                if hasattr(response, "headers"):
                    _provider = response.headers.get("X-LLMProxy-Provider", "")
                    _req_id = response.headers.get("X-LLMProxy-Request-Id", "")
                _in_tok = 0
                _out_tok = 0
                _cost_usd = 0.0
                _model_name = chat_body.get("model", "")
                try:
                    _data = json.loads(response.body)
                    _usage = _data.get("usage", {}) or {}
                    _in_tok = int(_usage.get("prompt_tokens", 0) or 0)
                    _out_tok = int(_usage.get("completion_tokens", 0) or 0)
                    _cost_usd = estimate_cost(_model_name, _in_tok, _out_tok)
                except (
                    json.JSONDecodeError,
                    AttributeError,
                    TypeError,
                    UnicodeDecodeError,
                ) as e:
                    logger.debug("Legacy completion usage parse skipped: %s", e)
                _status = (
                    response.status_code if hasattr(response, "status_code") else 200
                )
                _latency_ms = round(_duration * 1000, 1)
                if hasattr(agent.store, "log_spend"):
                    try:
                        await agent.store.log_spend(
                            ts=_now,
                            date=_date,
                            key_prefix=_key,
                            model=_model_name,
                            provider=_provider,
                            prompt_tokens=_in_tok,
                            completion_tokens=_out_tok,
                            cost_usd=_cost_usd,
                            latency_ms=_latency_ms,
                            status=_status,
                        )
                    except Exception as e:
                        logger.warning("Legacy completion spend log failed: %s", e)
                if hasattr(agent.store, "log_audit"):
                    try:
                        await agent.store.log_audit(
                            ts=_now,
                            req_id=_req_id,
                            session_id=session_id[:16],
                            key_prefix=_key,
                            model=_model_name,
                            provider=_provider,
                            status=_status,
                            prompt_tokens=_in_tok,
                            completion_tokens=_out_tok,
                            cost_usd=_cost_usd,
                            latency_ms=_latency_ms,
                            metadata="{}",
                        )
                        MetricsTracker.track_audit_persistence("completions", "ok")
                    except Exception as e:
                        MetricsTracker.track_audit_persistence("completions", "fail")
                        logger.warning("Legacy completion audit log failed: %s", e)
            except Exception as e:
                logger.warning("Legacy completion post-call persistence skipped: %s", e)

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
                    legacy_choices.append(
                        {
                            "text": msg.get("content", ""),
                            "index": i,
                            "logprobs": None,
                            "finish_reason": choice.get("finish_reason", "stop"),
                        }
                    )

                legacy_data = {
                    "id": data.get("id", ""),
                    "object": "text_completion",
                    "created": data.get("created", 0),
                    "model": data.get("model", ""),
                    "choices": legacy_choices,
                    "usage": data.get("usage", {}),
                }
                return JSONResponse(
                    content=legacy_data, status_code=response.status_code
                )
            except (json.JSONDecodeError, KeyError, IndexError):
                pass

        return response

    return router
