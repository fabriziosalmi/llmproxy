class MicroPrompts:
    """A collection of highly compressed prompts for tiny LLMs (SmolLM-135M)."""
    
    @staticmethod
    def extract_signature(json_payload: str) -> str:
        return (
            f"JSON:{json_payload}\n"
            "Task: List keys and data types only. Format: CSV. Shortest output."
        )

    @staticmethod
    def verify_llm(response_text: str) -> str:
        return (
            f"Text:{response_text[:200]}\n"
            "Task: Is this an LLM chat response? Answer YES or NO. Skip explanation."
        )

    @staticmethod
    def map_to_openai(signature: str) -> str:
        return (
            f"Sig:{signature}\n"
            "Task: Map to OpenAI (model, messages, content). Output JSON mapping code. No text."
        )

    @staticmethod
    def browser_step(objective: str, html_snippet: str) -> str:
        return (
            f"Goal:{objective}\n"
            f"HTML:{html_snippet[:300]}\n"
            "Task: Give CSS selector for target element. Final answer only."
        )
