"""
Model pricing table — per-million-token costs for budget tracking.

Prices are in USD per million tokens ($/MTok). Updated March 2026.
Override via config.yaml `pricing` section for custom/private deployments.
"""

from typing import Dict

# ── Static pricing table ($/MTok) ──
# Sources: official provider pricing pages as of March 2026

MODEL_PRICING: Dict[str, Dict[str, float]] = {
    # OpenAI
    "gpt-4o":                     {"input": 2.50,  "output": 10.00},
    "gpt-4o-mini":                {"input": 0.15,  "output": 0.60},
    "gpt-4.1":                    {"input": 2.00,  "output": 8.00},
    "gpt-4.1-mini":               {"input": 0.40,  "output": 1.60},
    "gpt-4.1-nano":               {"input": 0.10,  "output": 0.40},
    "o3-mini":                    {"input": 1.10,  "output": 4.40},
    "o3":                         {"input": 10.00, "output": 40.00},
    "o4-mini":                    {"input": 1.10,  "output": 4.40},

    # Anthropic (platform.claude.com/docs/en/about-claude/pricing)
    "claude-opus-4-20250514":     {"input": 5.00,  "output": 25.00},
    "claude-opus-4-6":            {"input": 5.00,  "output": 25.00},
    "claude-sonnet-4-20250514":   {"input": 3.00,  "output": 15.00},
    "claude-sonnet-4-6":          {"input": 3.00,  "output": 15.00},
    "claude-haiku-4-5-20251001":  {"input": 0.80,  "output": 4.00},

    # Google Gemini (ai.google.dev/gemini-api/docs/pricing)
    "gemini-2.5-pro":             {"input": 1.25,  "output": 10.00},
    "gemini-2.5-flash":           {"input": 0.30,  "output": 2.50},
    "gemini-2.0-flash":           {"input": 0.10,  "output": 0.40},

    # Groq (groq.com/pricing)
    "llama-3.3-70b-versatile":    {"input": 0.059, "output": 0.079},
    "llama-3.1-8b-instant":       {"input": 0.05,  "output": 0.08},
    "mixtral-8x7b-32768":         {"input": 0.24,  "output": 0.24},

    # DeepSeek V3.2 (api-docs.deepseek.com — unified pricing since Sep 2025)
    "deepseek-chat":              {"input": 0.28,  "output": 0.42},
    "deepseek-reasoner":          {"input": 0.28,  "output": 0.42},

    # xAI (docs.x.ai/developers/models)
    "grok-3":                     {"input": 3.00,  "output": 15.00},
    "grok-3-mini":                {"input": 0.30,  "output": 0.50},

    # Mistral (mistral.ai/pricing)
    "mistral-large-latest":       {"input": 2.00,  "output": 6.00},
    "mistral-small-latest":       {"input": 0.07,  "output": 0.20},
    "codestral-latest":           {"input": 0.30,  "output": 0.90},

    # Perplexity
    "sonar-pro":                  {"input": 3.00,  "output": 15.00},
    "sonar":                      {"input": 1.00,  "output": 1.00},

    # Together / Fireworks / SambaNova (Meta Llama hosted)
    "meta-llama/Llama-3.3-70B-Instruct-Turbo": {"input": 0.88, "output": 0.88},

    # Ollama / local — zero cost
    "llama3.3":                   {"input": 0.0,   "output": 0.0},
    "ollama/llama3.3":            {"input": 0.0,   "output": 0.0},
    "qwen3":                      {"input": 0.0,   "output": 0.0},
    "ollama/qwen3":               {"input": 0.0,   "output": 0.0},
    "phi-4":                      {"input": 0.0,   "output": 0.0},
    "ollama/phi-4":               {"input": 0.0,   "output": 0.0},
    "gemma3":                     {"input": 0.0,   "output": 0.0},
    "ollama/gemma3":              {"input": 0.0,   "output": 0.0},

    # ── Embedding models ──
    "text-embedding-3-small":     {"input": 0.02,  "output": 0.0},
    "text-embedding-3-large":     {"input": 0.13,  "output": 0.0},
    "text-embedding-ada-002":     {"input": 0.10,  "output": 0.0},
    "text-embedding-004":         {"input": 0.00,  "output": 0.0},  # Google free tier
    "mistral-embed":              {"input": 0.10,  "output": 0.0},
    "nomic-embed-text":           {"input": 0.0,   "output": 0.0},  # local
    "mxbai-embed-large":          {"input": 0.0,   "output": 0.0},  # local
}

# Default fallback for unknown models
_DEFAULT_PRICING = {"input": 1.00, "output": 3.00}

# Runtime config overrides (populated by RotatorAgent.setup() from config.yaml)
_config_overrides: Dict[str, Dict[str, float]] = {}

# Pre-sorted prefix list for O(log n) longest-prefix matching via bisect.
# Sorted longest-first so the first match is the most specific.
_SORTED_PREFIXES: list = sorted(MODEL_PRICING.keys(), key=len, reverse=True)


def set_config_overrides(overrides: Dict[str, Dict[str, float]]):
    """Apply pricing overrides from config.yaml at startup."""
    _config_overrides.update(overrides)


def get_pricing(model: str) -> Dict[str, float]:
    """Get pricing for a model. Priority: config override > exact match > longest prefix > default.

    Prefix matching is O(P) where P = number of known models (~40), but the
    list is sorted longest-first so the first hit is the most specific match
    (e.g. "gpt-4o-mini" matches before "gpt-4o" for "gpt-4o-mini-2025-01").
    Exact lookups are O(1) dict hits and cover the common case.
    """
    if model in _config_overrides:
        return _config_overrides[model]
    if model in MODEL_PRICING:
        return MODEL_PRICING[model]
    # Longest-prefix matching for versioned model names (e.g. "gpt-4o-2024-08-06")
    for prefix in _SORTED_PREFIXES:
        if model.startswith(prefix):
            return MODEL_PRICING[prefix]
    return _DEFAULT_PRICING


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Estimate cost in USD from token counts.

    Args:
        model: Model identifier (e.g. "gpt-4o", "claude-sonnet-4-20250514")
        prompt_tokens: Number of input/prompt tokens
        completion_tokens: Number of output/completion tokens

    Returns:
        Estimated cost in USD
    """
    pricing = get_pricing(model)
    return (prompt_tokens / 1_000_000) * pricing["input"] + \
           (completion_tokens / 1_000_000) * pricing["output"]


def estimate_cost_pre_flight(model: str, input_tokens: int,
                             avg_output_ratio: float = 0.5) -> float:
    """Pre-flight cost estimate (before we know actual output tokens).

    Used by SmartBudgetGuard to estimate cost before the request is sent.
    """
    pricing = get_pricing(model)
    est_output_tokens = int(input_tokens * avg_output_ratio)
    return (input_tokens / 1_000_000) * pricing["input"] + \
           (est_output_tokens / 1_000_000) * pricing["output"]
