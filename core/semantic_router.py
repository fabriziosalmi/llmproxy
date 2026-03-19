import re
from enum import Enum
from typing import List, Dict

class TaskComplexity(Enum):
    LIGHT = "light"       # Summarization, translation, simple Q&A
    MEDIUM = "medium"    # Coding snippets, creative writing
    HEAVY = "heavy"      # Complex reasoning, multi-step planning, analysis

class SemanticRouter:
    """Classifies incoming LLM requests based on inferred task complexity."""
    
    # Simple heuristic patterns
    PATTERNS = {
        TaskComplexity.LIGHT: [
            r"\b(summarize|translate|what is|tell me|hi|hello)\b",
            r"^.{0,100}$" # Short prompts
        ],
        TaskComplexity.HEAVY: [
            r"\b(reason|analyze|plan|architect|debug|optimize|solve|complex)\b",
            r"(?i)step by step",
            r"\b(proof|derivation|comprehensive)\b"
        ]
    }

    @staticmethod
    def classify(prompt: str) -> TaskComplexity:
        prompt_lower = prompt.lower()
        
        # Check heavy patterns first
        for pattern in SemanticRouter.PATTERNS[TaskComplexity.HEAVY]:
            if re.search(pattern, prompt_lower):
                return TaskComplexity.HEAVY
        
        # Check light patterns
        for pattern in SemanticRouter.PATTERNS[TaskComplexity.LIGHT]:
            if re.search(pattern, prompt_lower):
                return TaskComplexity.LIGHT
                
        return TaskComplexity.MEDIUM

    @staticmethod
    def get_preferred_model_tier(complexity: TaskComplexity) -> str:
        """Maps complexity to a model tier (e.g., 'smol', 'mid', 'large')."""
        mapping = {
            TaskComplexity.LIGHT: "smol",
            TaskComplexity.MEDIUM: "mid",
            TaskComplexity.HEAVY: "large"
        }
        return mapping[complexity]
