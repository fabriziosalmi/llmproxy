"""
Google Gemini API adapter.

Translates OpenAI chat/completions format ↔ Gemini generateContent format.

Key differences:
  - `messages` → `contents` with `parts` array
  - `role: "assistant"` → `role: "model"`
  - System message → `systemInstruction`
  - Model name is a URL path component, not body field
  - Auth: API key as `?key=` query param OR Bearer token
  - Response: `candidates[0].content.parts[0].text`
"""

import json
import time
import aiohttp
from typing import Dict, Any, AsyncGenerator, Tuple
from starlette.responses import Response
from .base import BaseModelAdapter


class GoogleAdapter(BaseModelAdapter):
    """Adapter for Google Gemini (generativelanguage.googleapis.com)."""

    provider_name = "google"

    def translate_embedding_request(
        self, base_url: str, body: Dict[str, Any], headers: Dict[str, str],
    ) -> Tuple[str, Dict[str, Any], Dict[str, str]]:
        """OpenAI /v1/embeddings → Gemini embedContent."""
        model = body.get("model", "text-embedding-004")
        url = f"{base_url.rstrip('/')}/models/{model}:embedContent"

        # Auth via header (not URL query param — keys in URLs leak to logs/referers)
        google_headers = dict(headers)
        auth = google_headers.pop("Authorization", "")
        if auth.startswith("Bearer "):
            google_headers["x-goog-api-key"] = auth[7:]
        google_headers["content-type"] = "application/json"

        # Translate input — OpenAI sends string or list of strings
        text_input = body.get("input", "")
        if isinstance(text_input, list):
            # Gemini embedContent handles one text at a time; use first for single call
            # For batch, caller should loop (or use batchEmbedContents)
            text_input = text_input[0] if text_input else ""

        gemini_body = {
            "model": f"models/{model}",
            "content": {"parts": [{"text": text_input}]},
        }

        return url, gemini_body, google_headers

    def translate_embedding_response(self, response_data: Dict[str, Any]) -> Dict[str, Any]:
        """Gemini embedContent response → OpenAI format."""
        if "error" in response_data:
            return response_data

        embedding = response_data.get("embedding", {})
        values = embedding.get("values", [])

        return {
            "object": "list",
            "data": [{
                "object": "embedding",
                "index": 0,
                "embedding": values,
            }],
            "model": response_data.get("model", ""),
            "usage": {
                "prompt_tokens": 0,  # Gemini doesn't report token counts for embeddings
                "total_tokens": 0,
            },
        }

    def translate_request(
        self, base_url: str, body: Dict[str, Any], headers: Dict[str, str],
    ) -> Tuple[str, Dict[str, Any], Dict[str, str]]:
        model = body.get("model", "gemini-2.5-flash")
        stream = body.get("stream", False)

        action = "streamGenerateContent" if stream else "generateContent"
        url = f"{base_url.rstrip('/')}/models/{model}:{action}"

        # Auth via header (not URL query param — keys in URLs leak to logs/referers)
        google_headers = dict(headers)
        auth = google_headers.pop("Authorization", "")
        if auth.startswith("Bearer "):
            google_headers["x-goog-api-key"] = auth[7:]
        if stream:
            url += "?" if "?" not in url else "&"
            url += "alt=sse"

        google_headers["content-type"] = "application/json"

        # Translate messages
        messages = body.get("messages", [])
        contents = []
        system_parts = []

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if role == "system":
                system_parts.append({"text": content})
                continue

            gemini_role = "model" if role == "assistant" else "user"

            # Handle multimodal content
            if isinstance(content, list):
                parts = _translate_multimodal_parts(content)
            else:
                parts = [{"text": content}]

            contents.append({"role": gemini_role, "parts": parts})

        gemini_body: Dict[str, Any] = {"contents": contents}

        if system_parts:
            gemini_body["systemInstruction"] = {"parts": system_parts}

        # Generation config
        gen_config: Dict[str, Any] = {}
        if body.get("temperature") is not None:
            gen_config["temperature"] = body["temperature"]
        if body.get("top_p") is not None:
            gen_config["topP"] = body["top_p"]
        if body.get("max_tokens") or body.get("max_completion_tokens"):
            gen_config["maxOutputTokens"] = body.get("max_tokens") or body.get("max_completion_tokens")
        if body.get("stop"):
            gen_config["stopSequences"] = body["stop"] if isinstance(body["stop"], list) else [body["stop"]]
        if gen_config:
            gemini_body["generationConfig"] = gen_config

        # Tool use
        if body.get("tools"):
            gemini_body["tools"] = [{"functionDeclarations": _translate_tools(body["tools"])}]

        return url, gemini_body, google_headers

    def translate_response(self, response_data: Dict[str, Any]) -> Dict[str, Any]:
        """Gemini response → OpenAI format."""
        if "error" in response_data:
            return response_data

        candidates = response_data.get("candidates", [])
        if not candidates:
            return {"error": {"message": "No candidates in response", "type": "api_error"}}

        candidate = candidates[0]
        content = candidate.get("content", {})
        parts = content.get("parts", [])

        text_parts = []
        tool_calls = []
        for i, part in enumerate(parts):
            if "text" in part:
                text_parts.append(part["text"])
            elif "functionCall" in part:
                fc = part["functionCall"]
                tool_calls.append({
                    "id": f"call_{i}",
                    "type": "function",
                    "function": {
                        "name": fc.get("name", ""),
                        "arguments": json.dumps(fc.get("args", {})),
                    },
                })

        message: Dict[str, Any] = {
            "role": "assistant",
            "content": "\n".join(text_parts) if text_parts else None,
        }
        if tool_calls:
            message["tool_calls"] = tool_calls

        # Map finish reason
        finish_reason_map = {
            "STOP": "stop",
            "MAX_TOKENS": "length",
            "SAFETY": "content_filter",
            "RECITATION": "content_filter",
        }
        gemini_finish = candidate.get("finishReason", "STOP")

        usage_meta = response_data.get("usageMetadata", {})

        return {
            "id": f"gemini-{int(time.time())}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": response_data.get("modelVersion", ""),
            "choices": [{
                "index": 0,
                "message": message,
                "finish_reason": finish_reason_map.get(gemini_finish, "stop"),
            }],
            "usage": {
                "prompt_tokens": usage_meta.get("promptTokenCount", 0),
                "completion_tokens": usage_meta.get("candidatesTokenCount", 0),
                "total_tokens": usage_meta.get("totalTokenCount", 0),
            },
        }

    def translate_stream_chunk(self, chunk: bytes) -> bytes:
        """Translate Gemini SSE chunks to OpenAI SSE format."""
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
                    candidates = data.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        text_content = ""
                        for part in parts:
                            if "text" in part:
                                text_content += part["text"]

                        if text_content:
                            openai_chunk = {
                                "id": f"gemini-{int(time.time())}",
                                "object": "chat.completion.chunk",
                                "created": int(time.time()),
                                "model": "",
                                "choices": [{
                                    "index": 0,
                                    "delta": {"content": text_content},
                                    "finish_reason": None,
                                }],
                            }
                            output_lines.append(f"data: {json.dumps(openai_chunk)}\n\n")

                        finish_reason = candidates[0].get("finishReason")
                        if finish_reason and finish_reason != "STOP":
                            pass  # handled at end
                        elif finish_reason == "STOP":
                            openai_chunk = {
                                "id": f"gemini-{int(time.time())}",
                                "object": "chat.completion.chunk",
                                "created": int(time.time()),
                                "model": "",
                                "choices": [{
                                    "index": 0,
                                    "delta": {},
                                    "finish_reason": "stop",
                                }],
                            }
                            output_lines.append(f"data: {json.dumps(openai_chunk)}\n\n")
                            output_lines.append("data: [DONE]\n\n")

                except (json.JSONDecodeError, KeyError):
                    pass

        return "".join(output_lines).encode("utf-8") if output_lines else b""

    _REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=60, sock_read=55)

    async def request(
        self,
        url: str,
        body: Dict[str, Any],
        headers: Dict[str, str],
        session: aiohttp.ClientSession,
    ) -> Response:
        async with session.post(url, json=body, headers=headers, timeout=self._REQUEST_TIMEOUT) as resp:
            content = await resp.read()
            status = resp.status

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
        async with session.post(url, json=body, headers=headers, timeout=self._REQUEST_TIMEOUT) as resp:
            async for chunk in resp.content.iter_any():
                translated = self.translate_stream_chunk(chunk)
                if translated:
                    yield translated


def _translate_multimodal_parts(content_list: list) -> list:
    """Translate OpenAI multimodal content array to Gemini parts.

    Handles:
      - text parts → {"text": "..."}
      - image_url with data: URI → {"inlineData": {"mimeType": ..., "data": ...}}
      - image_url with http(s): URL → {"fileData": {"mimeType": "image/...", "fileUri": ...}}
    """
    parts = []
    for part in content_list:
        part_type = part.get("type", "")
        if part_type == "text":
            parts.append({"text": part.get("text", "")})
        elif part_type == "image_url":
            url = part.get("image_url", {}).get("url", "")
            if url.startswith("data:"):
                # Base64 inline: data:image/png;base64,iVBOR...
                try:
                    header, data = url[5:].split(";base64,", 1)
                    parts.append({
                        "inlineData": {
                            "mimeType": header,
                            "data": data,
                        },
                    })
                except ValueError:
                    parts.append({"text": f"[image: {url[:100]}]"})
            else:
                # URL reference — Gemini uses fileData for URLs
                # Infer MIME type from extension
                mime = "image/jpeg"
                lower_url = url.lower()
                if ".png" in lower_url:
                    mime = "image/png"
                elif ".gif" in lower_url:
                    mime = "image/gif"
                elif ".webp" in lower_url:
                    mime = "image/webp"
                parts.append({
                    "fileData": {
                        "mimeType": mime,
                        "fileUri": url,
                    },
                })
        else:
            parts.append({"text": str(part)})

    return parts or [{"text": ""}]


def _translate_tools(openai_tools: list) -> list:
    """OpenAI tool format → Gemini functionDeclarations."""
    declarations = []
    for tool in openai_tools:
        if tool.get("type") == "function":
            fn = tool["function"]
            declarations.append({
                "name": fn["name"],
                "description": fn.get("description", ""),
                "parameters": fn.get("parameters", {"type": "object", "properties": {}}),
            })
    return declarations
