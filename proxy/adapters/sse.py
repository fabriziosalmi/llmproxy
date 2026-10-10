"""Server-sent events arrive as bytes, not as events.

A TCP read returns whatever is in the socket buffer: half an event, three and a
half, or one cut inside a multi-byte character. The stream translators parsed
each read on its own and dropped whatever did not parse, so an event that
straddled two reads vanished from the client's answer (a word, a tool-call
fragment, the final ``[DONE]``) with no error anywhere, and the forwarder lost
the usage record whenever that was the event cut in two.

``SSEReassembler`` holds the bytes that do not yet end an event and hands back
only whole events, in order.
"""

from __future__ import annotations

import json

#: An event that has not ended after this many bytes is handed back as it is
#: rather than buffered further: a peer that never sends the blank line must
#: not be able to grow the buffer without bound.
MAX_EVENT_BYTES = 1024 * 1024

_LF = b"\n\n"
_CRLF = b"\r\n\r\n"


class SSEReassembler:
    """Whole events out of arbitrary reads. One per stream; not shared."""

    __slots__ = ("_buf",)

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, chunk: bytes) -> list[bytes]:
        """The events ``chunk`` completes, each normalised to end in ``\\n\\n``."""
        if chunk:
            self._buf += chunk
        events: list[bytes] = []
        while True:
            lf = self._buf.find(_LF)
            crlf = self._buf.find(_CRLF)
            if lf < 0 and crlf < 0:
                break
            if crlf >= 0 and (lf < 0 or crlf < lf):
                end, skip = crlf, len(_CRLF)
            else:
                end, skip = lf, len(_LF)
            body = bytes(self._buf[:end])
            del self._buf[: end + skip]
            if body.strip():
                events.append(body.replace(b"\r\n", b"\n") + _LF)
        if len(self._buf) > MAX_EVENT_BYTES:
            events.append(bytes(self._buf))
            self._buf.clear()
        return events

    def flush(self) -> bytes:
        """What is left when the stream ends without a closing blank line."""
        rest = bytes(self._buf)
        self._buf.clear()
        return rest.replace(b"\r\n", b"\n") if rest.strip() else b""


def usage_chunk(prompt_tokens: int, completion_tokens: int) -> str:
    """The usage record in the shape OpenAI sends it: a chunk with no choices."""
    return "data: " + json.dumps(
        {
            "id": "",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "",
            "choices": [],
            "usage": {
                "prompt_tokens": int(prompt_tokens),
                "completion_tokens": int(completion_tokens),
                "total_tokens": int(prompt_tokens) + int(completion_tokens),
            },
        },
        separators=(",", ":"),
    ) + "\n\n"
