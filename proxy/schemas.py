"""Request bodies, validated at the boundary.

FastAPI is used throughout and Pydantic is a direct dependency, but no handler
that accepts caller input declared a request model: chat, completions and
embeddings each did `await request.json()` and then reached into the dict with
`.get()`. So the type of every field was discovered by whatever eventually used
it, several layers in.

The consequences were ordinary and real. `{"model": {"a": 1}}` was accepted at
the edge, written into the audit and spend rows, used as a dict key in the cost
estimator and passed to the tokenizer, so the failure surfaced as a TypeError
from a module the caller had never heard of rather than a 422 naming the field.
`{"messages": "hello"}` passed the max_messages check — len() of a string is a
number — and was then iterated character by character by the token counter. And
the generated OpenAPI document carried no request shapes at all, so it
documented nothing a client could validate against.

Two design constraints, both load-bearing:

  * `extra="allow"`. These are OpenAI-compatible endpoints and callers send
    parameters this proxy does not model — temperature, top_p, tool_choice,
    provider-specific extensions. Rejecting them would break every real client.
    They are carried through untouched.
  * Handlers keep receiving a plain dict, produced with
    `model_dump(exclude_unset=True)`. Only what the caller actually sent is
    forwarded, which is exactly what passing the raw parsed body did — so
    validation adds a gate without changing what reaches the upstream.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


class ChatMessage(BaseModel):
    """One message in a chat completion.

    `content` is deliberately permissive: the OpenAI schema allows a string or
    a list of content parts (text, image_url), and this proxy translates the
    multimodal form for several providers. Narrowing it here would reject
    requests the adapters handle correctly.
    """

    model_config = ConfigDict(extra="allow")

    role: str
    content: Optional[Union[str, List[Dict[str, Any]]]] = None


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str
    messages: List[ChatMessage]
    stream: Optional[bool] = None

    def to_body(self) -> Dict[str, Any]:
        """The dict the pipeline expects, carrying only what the caller sent."""
        return self.model_dump(exclude_unset=True, exclude_none=False)


class CompletionRequest(BaseModel):
    """Legacy /v1/completions. `prompt` may be a string or a list of them."""

    model_config = ConfigDict(extra="allow")

    model: str
    prompt: Optional[Union[str, List[str]]] = None
    stream: Optional[bool] = None

    def to_body(self) -> Dict[str, Any]:
        return self.model_dump(exclude_unset=True, exclude_none=False)


class EmbeddingsRequest(BaseModel):
    """`input` may be a string, a list of strings, or pre-tokenised integers."""

    model_config = ConfigDict(extra="allow")

    model: str
    input: Union[str, List[str], List[int], List[List[int]]] = Field(...)

    def to_body(self) -> Dict[str, Any]:
        return self.model_dump(exclude_unset=True, exclude_none=False)
