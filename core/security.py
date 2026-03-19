import re
import logging
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)

class SecurityShield:
    """Orchestrates security checks for incoming LLM requests."""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config.get("security", {})
        self.enabled = self.config.get("enabled", True)
        
    def inspect(self, body: Dict[str, Any]) -> Optional[str]:
        """
        Inspects the request body for security violations.
        Returns an error message if a violation is found, else None.
        """
        if not self.enabled:
            return None
            
        # 1. Payload Guard (Flooding/Size)
        flood_error = self._check_payload_flooding(body)
        if flood_error: return flood_error
        
        # 2. Injection Detector (Prompt/Hidden)
        prompt = self._extract_prompt(body)
        if prompt:
            injection_error = self._check_injections(prompt)
            if injection_error: return injection_error
            
            # 3. Link Sanitizer
            link_error = self._check_links(prompt)
            if link_error: return link_error
            
        return None

    def _extract_prompt(self, body: Dict[str, Any]) -> str:
        messages = body.get("messages", [])
        if messages:
            return messages[-1].get("content", "")
        return body.get("prompt", "")

    def _check_payload_flooding(self, body: Dict[str, Any]) -> Optional[str]:
        max_size = self.config.get("max_payload_size_kb", 512) * 1024
        if len(str(body)) > max_size:
            return "Payload too large (potential body flooding attack)"
            
        # Check for message count flooding
        if len(body.get("messages", [])) > self.config.get("max_messages", 50):
            return "Too many messages in conversation (resource stalling protection)"
        
        # Check for excessive repetition in the prompt
        prompt = self._extract_prompt(body)
        if len(prompt) > 2000 and len(set(prompt.split())) / len(prompt.split()) < 0.2:
            return "High entropy/repetition detected (potential payload flooding)"
            
        return None

    def _check_injections(self, prompt: str) -> Optional[str]:
        patterns = [
            r"(?i)ignore all previous instructions",
            r"(?i)system prompt",
            r"(?i)you are now a",
            r"(?i)bypass",
            r"(?i)reveal your (secret|hidden|base) instructions",
            r"(?i)assistant: ", # Attempting to close previous turn
            r"\[.*?\]" # Hidden instructions in brackets
        ]
        
        for pattern in patterns:
            if re.search(pattern, prompt):
                return f"Potential prompt injection detected (pattern: {pattern})"
        return None

    def _check_links(self, prompt: str) -> Optional[str]:
        if not self.config.get("link_sanitization", {}).get("enabled", True):
            return None
            
        # Improved link detection
        links = re.findall(r"http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+", prompt)
        blocked_domains = self.config.get("link_sanitization", {}).get("blocked_domains", [])
        
        for link in links:
            if any(domain in link for domain in blocked_domains):
                return f"Unsafe link detected: {link}"
                
        return None
