import re
import logging
from enum import Enum
from typing import List, Dict, Optional

class TaskComplexity(Enum):
    LIGHT = "light"       # Summarization, translation, simple Q&A
    MEDIUM = "medium"    # Coding snippets, creative writing
    HEAVY = "heavy"      # Complex reasoning, multi-step planning, analysis

class SemanticRouter:
    """Classifies incoming LLM requests based on inferred task complexity."""
    
    def __init__(self, assistant=None):
        self.assistant = assistant
        self.logger = logging.getLogger(__name__)

    # Simple heuristic patterns (Fallback)
    PATTERNS = {
        TaskComplexity.LIGHT: [
            r"\b(summarize|translate|what is|tell me|hi|hello)\b",
            r"^.{0,50}$" # Short prompts
        ],
        TaskComplexity.HEAVY: [
            r"\b(reason|analyze|plan|architect|debug|optimize|solve|complex)\b",
            r"(?i)step by step",
            r"\b(proof|derivation|comprehensive)\b"
        ]
    }

    async def classify(self, prompt: str) -> TaskComplexity:
        """Classifies the prompt using AI if available, else falls back to heuristics."""
        if self.assistant:
            try:
                ai_result = await self._classify_with_ai(prompt)
                if ai_result:
                    return ai_result
            except Exception as e:
                self.logger.warning(f"AI classification failed: {e}. Falling back to heuristics.")
        
        return self._classify_heuristically(prompt)

    async def _classify_with_ai(self, prompt: str) -> Optional[TaskComplexity]:
        sys_prompt = (
            "Classify the following LLM prompt into one of these three complexities: "
            "LIGHT (simple Q&A, greetings, translation, short summary), "
            "MEDIUM (creative writing, standard coding tasks, general analysis), "
            "HEAVY (complex debugging, strategic planning, mathematical proofs, architectural design). "
            "Respond ONLY with the word: LIGHT, MEDIUM, or HEAVY."
        )
        response = await self.assistant.consult(f"{sys_prompt}\n\nPrompt: {prompt}")
        if response:
            clean_resp = response.strip().upper()
            for complexity in TaskComplexity:
                if complexity.name in clean_resp:
                    return complexity
        return None

    def _classify_heuristically(self, prompt: str) -> TaskComplexity:
        prompt_lower = prompt.lower()
        
        # Check heavy patterns first
        for pattern in self.PATTERNS[TaskComplexity.HEAVY]:
            if re.search(pattern, prompt_lower):
                return TaskComplexity.HEAVY
        
        # Check light patterns
        for pattern in self.PATTERNS[TaskComplexity.LIGHT]:
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

    async def decompose_prompt(self, prompt: str) -> List[str]:
        """Decomposes a complex prompt into parallel sub-tasks for the 'Olimpo' expansion."""
        if not self.assistant or len(prompt) < 100:
            return [prompt]
            
        sys_prompt = (
            "Analyze the user request and decompose it into 2-3 independent sub-tasks "
            "that can be executed in parallel by different LLMs. "
            "Respond ONLY with a valid JSON array of strings. Example: ['Analyze text A', 'Summarize part B']"
        )
        
        try:
            response = await self.assistant.consult(f"{sys_prompt}\n\nPrompt: {prompt}")
            import json
            match = re.search(r"\[.*\]", response, re.DOTALL)
            if match:
                return json.loads(match.group())
        except Exception as e:
            self.logger.warning(f"Decomposition failed: {e}")
            
        return [prompt]

    async def fuse_responses(self, original_prompt: str, sub_responses: List[str]) -> str:
        """Fuses parallel sub-responses into a single coherent output."""
        if not self.assistant or len(sub_responses) <= 1:
            return sub_responses[0] if sub_responses else ""
            
        fusion_prompt = f"Original Intent: {original_prompt}\n\nCollected Sub-tasks results:\n"
        for i, res in enumerate(sub_responses):
            fusion_prompt += f"Result {i+1}: {res}\n---\n"
            
        sys_prompt = (
            "Fuse the following results into one high-quality, professional response. "
            "Ensure consistency and remove redundancies. Respond with the final text only."
        )
        
        try:
            return await self.assistant.consult(f"{sys_prompt}\n\n{fusion_prompt}")
        except Exception as e:
            self.logger.warning(f"Fusion failed: {e}")
            return "\n\n".join(sub_responses)
