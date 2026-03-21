"""
Anthropic Messages API adapter.

Translates OpenAI chat/completions format ↔ Anthropic /v1/messages format.

Key differences:
  - System message extracted to top-level `system` param
  - `max_tokens` required (defaults to 4096)
  - Auth via `x-api-key` header (not Bearer)
  - Response: `content[0].text` → `choices[0].message.content`
  - Streaming: `event: content_block_delta` with `delta.text`
"""

import json
import time
import aiohttp
from typing import Dict, Any, AsyncGenerator, Tuple
from starlette.responses import Response
from .base import BaseModelAdapter


class AnthropicAdapter(BaseModelAdapter):
    """Adapter for Anthropic Messages API."""

    provider_name = "anthropic"
    supports_embeddings = False  # Anthropic has no embeddings API
    API_VERSION = "2023-06-01"

    def translate_request(
        self, base_url: str, body: Dict[str, Any], headers: Dict[str, str],
    ) -> Tuple[str, Dict[str, Any], Dict[str, str]]:
        url = f"{base_url.rstrip('/')}/messages"

        # Extract system message and translate multimodal content
        messages = list(body.get("messages", []))
        system_text = None
        filtered = []
        for msg in messages:
            if msg.get("role") == "system":
                # Anthropic takes system as a top-level param
                system_text = msg.get("content", "")
            else:
                translated_msg = {"role": msg.get("role", "user")}
                translated_msg["content"] = _translate_content(msg.get("content", ""))
                filtered.append(translated_msg)

        anthropic_body = {
            "model": body.get("model", "claude-sonnet-4-20250514"),
            "messages": filtered,
            "max_tokens": body.get("max_tokens") or body.get("max_completion_tokens") or 4096,
        }

        if system_text:
            anthropic_body["system"] = system_text

        # Optional params — only include if set
        if body.get("temperature") is not None:
            anthropic_body["temperature"] = body["temperature"]
        if body.get("top_p") is not None:
            anthropic_body["top_p"] = body["top_p"]
        if body.get("stop"):
            anthropic_body["stop_sequences"] = body["stop"] if isinstance(body["stop"], list) else [body["stop"]]
        if body.get("stream"):
            anthropic_body["stream"] = True

        # Tool use translation (OpenAI → Anthropic format)
        if body.get("tools"):
            anthropic_body["tools"] = _translate_tools(body["tools"])
        if body.get("tool_choice"):
            anthropic_body["tool_choice"] = _translate_tool_choice(body["tool_choice"])

        # Auth: x-api-key instead of Bearer
        anthropic_headers = dict(headers)
        auth = anthropic_headers.pop("Authorization", "")
        if auth.startswith("Bearer "):
            anthropic_headers["x-api-key"] = auth[7:]
        anthropic_headers["anthropic-version"] = self.API_VERSION
        anthropic_headers["content-type"] = "application/json"

        return url, anthropic_body, anthropic_headers

    def translate_response(self, response_data: Dict[str, Any]) -> Dict[str, Any]:
        """Anthropic response → OpenAI format."""
        if "error" in response_data:
            return response_data

        # Extract text content from Anthropic response
        content_blocks = response_data.get("content", [])
        text_parts = []
        tool_calls = []

        for i, block in enumerate(content_blocks):
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                tool_calls.append({
                    "id": block.get("id", f"call_{i}"),
                    "type": "function",
                    "function": {
                        "name": block.get("name", ""),
                        "arguments": json.dumps(block.get("input", {})),
                    },
                })

        message = {
            "role": "assistant",
            "content": "\n".join(text_parts) if text_parts else None,
        }
        if tool_calls:
            message["tool_calls"] = tool_calls

        # Map stop reason
        stop_reason = response_data.get("stop_reason", "end_turn")
        finish_reason_map = {
            "end_turn": "stop",
            "max_tokens": "length",
            "stop_sequence": "stop",
            "tool_use": "tool_calls",
        }

        usage = response_data.get("usage", {})

        return {
            "id": response_data.get("id", ""),
            "object": "chat.completion",
            "created": int(time.time()),
            "model": response_data.get("model", ""),
            "choices": [{
                "index": 0,
                "message": message,
                "finish_reason": finish_reason_map.get(stop_reason, "stop"),
            }],
            "usage": {
                "prompt_tokens": usage.get("input_tokens", 0),
                "completion_tokens": usage.get("output_tokens", 0),
                "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
            },
        }

    def translate_stream_chunk(self, chunk: bytes) -> bytes:
        """Translate Anthropic SSE chunks to OpenAI SSE format."""
        text = chunk.decode("utf-8", errors="replace")
        lines = text.strip().split("\n")
        output_lines = []

        for line in lines:
            if line.startswith("data: "):
                data_str = line[6:]
                if data_str.strip() == "[DONE]":
                    output_lines.append("data: [DONE]\n\n")
                    continue
                try:
                    data = json.loads(data_str)
                    event_type = data.get("type", "")

                    if event_type == "content_block_delta":
                        delta = data.get("delta", {})
                        if delta.get("type") == "text_delta":
                            openai_chunk = {
                                "id": data.get("message", {}).get("id", ""),
                                "object": "chat.completion.chunk",
                                "created": int(time.time()),
                                "model": "",
                                "choices": [{
                                    "index": 0,
                                    "delta": {"content": delta.get("text", "")},
                                    "finish_reason": None,
                                }],
                            }
                            output_lines.append(f"data: {json.dumps(openai_chunk)}\n\n")

                    elif event_type == "message_delta":
                        stop_reason = data.get("delta", {}).get("stop_reason")
                        if stop_reason:
                            finish_map = {"end_turn": "stop", "max_tokens": "length", "tool_use": "tool_calls"}
                            openai_chunk = {
                                "id": "",
                                "object": "chat.completion.chunk",
                                "created": int(time.time()),
                                "model": "",
                                "choices": [{
                                    "index": 0,
                                    "delta": {},
                                    "finish_reason": finish_map.get(stop_reason, "stop"),
                                }],
                            }
                            output_lines.append(f"data: {json.dumps(openai_chunk)}\n\n")

                    elif event_type == "message_stop":
                        output_lines.append("data: [DONE]\n\n")

                except (json.JSONDecodeError, KeyError):
                    pass
            elif line.startswith("event: "):
                # Skip Anthropic event type lines — OpenAI doesn't use them
                pass

        return "".join(output_lines).encode("utf-8") if output_lines else b""

    async def request(
        self,
        url: str,
        body: Dict[str, Any],
        headers: Dict[str, str],
        session: aiohttp.ClientSession,
    ) -> Response:
        async with session.post(url, json=body, headers=headers) as resp:
            content = await resp.read()
            status = resp.status

        # Translate response body to OpenAI format
        if status == 200:
            try:
                data = json.loads(content)
                translated = self.translate_response(data)
                content = json.dumps(translated).encode("utf-8")
            except (json.JSONDecodeError, KeyError):
                pass

        return Response(content=content, status_code=status, media_type="application/json")

    async def stream(
        self,
        url: str,
        body: Dict[str, Any],
        headers: Dict[str, str],
        session: aiohttp.ClientSession,
    ) -> AsyncGenerator[bytes, None]:
        async with session.post(url, json=body, headers=headers) as resp:
            async for chunk in resp.content.iter_any():
                translated = self.translate_stream_chunk(chunk)
                if translated:
                    yield translated


def _translate_content(content):
    """Translate OpenAI message content to Anthropic content blocks.

    Handles:
      - String content → string passthrough (Anthropic accepts both)
      - List content with text/image_url parts → Anthropic content blocks
        - image_url with data: URI → base64 source
        - image_url with http(s): URL → url source
    """
    if isinstance(content, str):
        return content

    if not isinstance(content, list):
        return str(content)

    blocks = []
    for part in content:
        part_type = part.get("type", "")
        if part_type == "text":
            blocks.append({"type": "text", "text": part.get("text", "")})
        elif part_type == "image_url":
            url = part.get("image_url", {}).get("url", "")
            if url.startswith("data:"):
                # Base64 inline: data:image/png;base64,iVBOR...
                try:
                    header, data = url[5:].split(";base64,", 1)
                    media_type = header  # e.g. "image/png"
                    blocks.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": data,
                        },
                    })
                except ValueError:
                    # Malformed data URI — pass as text fallback
                    blocks.append({"type": "text", "text": f"[image: {url[:100]}]"})
            else:
                # URL reference
                blocks.append({
                    "type": "image",
                    "source": {"type": "url", "url": url},
                })
        else:
            # Unknown part type — pass as text
            blocks.append({"type": "text", "text": str(part)})

    return blocks or [{"type": "text", "text": ""}]


def _translate_tools(openai_tools: list) -> list:
    """OpenAI tool format → Anthropic tool format."""
    anthropic_tools = []
    for tool in openai_tools:
        if tool.get("type") == "function":
            fn = tool["function"]
            anthropic_tools.append({
                "name": fn["name"],
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
            })
    return anthropic_tools


def _translate_tool_choice(choice) -> dict:
    """OpenAI tool_choice → Anthropic tool_choice."""
    if choice == "auto":
        return {"type": "auto"}
    elif choice == "none":
        return {"type": "none"}
    elif choice == "required":
        return {"type": "any"}
    elif isinstance(choice, dict) and choice.get("type") == "function":
        return {"type": "tool", "name": choice["function"]["name"]}
    return {"type": "auto"}
