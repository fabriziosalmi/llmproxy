"""
Accurate token counting with tiktoken (optional) or char-heuristic fallback.

tiktoken is a Rust-backed BPE tokenizer that gives exact OpenAI token counts.
For non-OpenAI models (Claude, Gemini, Mistral), cl100k_base is a reasonable
approximation (within ~5%). Falls back to len(text)//4 if tiktoken is absent.

Install: pip install tiktoken
"""

import logging
from typing import List, Dict, Any

logger = logging.getLogger("llmproxy.tokenizer")

try:
    import tiktoken
    _TIKTOKEN_AVAILABLE = True
    logger.info("tiktoken available — using accurate BPE token counting")
except ImportError:
    _TIKTOKEN_AVAILABLE = False
    logger.info("tiktoken not installed — using char-heuristic (pip install tiktoken for accuracy)")

# Model → tiktoken encoding name
_ENCODING_MAP = {
    # OpenAI o200k_base (GPT-4o, o-series, GPT-4.1)
    "gpt-4o": "o200k_base",
    "gpt-4o-mini": "o200k_base",
    "gpt-4.1": "o200k_base",
    "gpt-4.1-mini": "o200k_base",
    "gpt-4.1-nano": "o200k_base",
    "o1": "o200k_base",
    "o1-mini": "o200k_base",
    "o1-preview": "o200k_base",
    "o3": "o200k_base",
    "o3-mini": "o200k_base",
    "o4-mini": "o200k_base",
    # OpenAI cl100k_base (older GPT-4, embeddings)
    "gpt-4": "cl100k_base",
    "gpt-4-turbo": "cl100k_base",
    "gpt-3.5-turbo": "cl100k_base",
    "text-embedding-3-small": "cl100k_base",
    "text-embedding-3-large": "cl100k_base",
    "text-embedding-ada-002": "cl100k_base",
}

# Default encoding for non-OpenAI models (Claude, Gemini, Mistral, etc.)
# cl100k_base is a good approximation (±5%) for most modern tokenizers
_DEFAULT_ENCODING = "cl100k_base"

# Encoding cache (tiktoken.get_encoding is idempotent but we avoid repeated lookups)
_encoding_cache = {}


def _get_encoding(model: str):
    """Get tiktoken encoding for a model (cached)."""
    encoding_name = _ENCODING_MAP.get(model, _DEFAULT_ENCODING)
    # Try prefix matching for versioned models (e.g. "gpt-4o-2024-08-06")
    if model not in _ENCODING_MAP:
        for known, enc in _ENCODING_MAP.items():
            if model.startswith(known):
                encoding_name = enc
                break

    if encoding_name not in _encoding_cache:
        _encoding_cache[encoding_name] = tiktoken.get_encoding(encoding_name)
    return _encoding_cache[encoding_name]


def count_tokens(text: str, model: str = "") -> int:
    """Count tokens in a text string.

    Uses tiktoken for exact count when available, else len(text)//4 heuristic.
    """
    if not text:
        return 0

    if not _TIKTOKEN_AVAILABLE:
        return max(1, len(text) // 4)

    try:
        enc = _get_encoding(model)
        return len(enc.encode(text, disallowed_special=()))
    except Exception:
        return max(1, len(text) // 4)


def count_messages_tokens(messages: List[Dict[str, Any]], model: str = "") -> int:
    """Count tokens for a full messages array including per-message overhead.

    Accounts for role tokens and structural overhead (~4 tokens per message).
    Image content blocks are estimated at 85 tokens (OpenAI low-detail).
    """
    total = 0
    for msg in messages:
        total += 4  # per-message overhead (role + structural tokens)
        content = msg.get("content", "")
        if isinstance(content, str):
            total += count_tokens(content, model)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        total += count_tokens(block.get("text", ""), model)
                    elif block.get("type") == "image_url":
                        total += 85  # low-detail image flat estimate
    total += 2  # conversation priming tokens
    return total


def is_tiktoken_available() -> bool:
    """Check if tiktoken is installed."""
    return _TIKTOKEN_AVAILABLE
