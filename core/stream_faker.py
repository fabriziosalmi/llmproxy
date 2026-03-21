"""
LLMPROXY — SSE Stream Faker

Converts a cached JSON response (full OpenAI-format) into a Server-Sent Events
stream that matches the OpenAI streaming API format exactly.

This is critical for clients using `stream: true` (LangChain, OpenAI SDK, etc.).
Without this, a cached response would break streaming clients.

Design:
  - No artificial delay. Speed is a feature, not a bug.
  - `asyncio.sleep(0)` between chunks to yield control (don't starve event loop).
  - Produces standard SSE: `data: {...}\n\n` with `data: [DONE]\n\n` terminator.
"""

import json
import asyncio
from typing import AsyncGenerator, Dict, Any


async def fake_stream(cached_response: Dict[str, Any]) -> AsyncGenerator[bytes, None]:
    """Convert a cached response dict into OpenAI-compatible SSE chunks.

    Input: Full response dict with choices[0].message.content
    Output: SSE byte chunks matching OpenAI streaming format:
        data: {"id":"...","choices":[{"delta":{"content":"token"},"index":0}]}\n\n

    Args:
        cached_response: The full cached response dict (OpenAI format)

    Yields:
        bytes: SSE-formatted chunks
    """
    # Extract content from cached response
    choices = cached_response.get("choices", [])
    if not choices:
        yield b"data: [DONE]\n\n"
        return

    content = choices[0].get("message", {}).get("content", "")
    model = cached_response.get("model", "cached")
    response_id = cached_response.get("id", "chatcmpl-cached")

    if not content:
        yield b"data: [DONE]\n\n"
        return

    # First chunk: role
    first_chunk = {
        "id": response_id,
        "object": "chat.completion.chunk",
        "model": model,
        "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}],
    }
    yield f"data: {json.dumps(first_chunk)}\n\n".encode()
    await asyncio.sleep(0)

    # Content chunks: split into ~4-char tokens (matches typical tokenizer granularity)
    chunk_size = 4
    for i in range(0, len(content), chunk_size):
        token = content[i : i + chunk_size]
        chunk = {
            "id": response_id,
            "object": "chat.completion.chunk",
            "model": model,
            "choices": [{"index": 0, "delta": {"content": token}, "finish_reason": None}],
        }
        yield f"data: {json.dumps(chunk)}\n\n".encode()
        # Yield control to event loop every 16 chunks (~64 chars)
        # to avoid starving other coroutines on large responses
        if (i // chunk_size) % 16 == 15:
            await asyncio.sleep(0)

    # Final chunk: finish_reason
    final_chunk = {
        "id": response_id,
        "object": "chat.completion.chunk",
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    yield f"data: {json.dumps(final_chunk)}\n\n".encode()
    yield b"data: [DONE]\n\n"
