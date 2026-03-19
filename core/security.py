import re
import logging
import unicodedata
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
        """Unicode-normalized, scored injection detection."""
        # Normalize to prevent homoglyph/encoding bypass
        normalized = unicodedata.normalize("NFKC", prompt).lower()
        
        # Weighted threat patterns (score >= 0.7 triggers block)
        threats = [
            (r"ignore\s+(all\s+)?previous\s+instructions?", 0.9),
            (r"system\s*prompt", 0.8),
            (r"you\s+are\s+now\s+a", 0.7),
            (r"bypass\s+(all\s+)?(safety|security|filter)", 0.85),
            (r"reveal\s+(your\s+)?(secret|hidden|base)\s+instructions?", 0.9),
            (r"```\s*system", 0.85),
            (r"<\|im_start\|>", 0.95),  # ChatML injection
            (r"assistant:\s", 0.6),  # Turn-closing attempt
        ]
        
        score = 0.0
        matched = []
        for pattern, weight in threats:
            if re.search(pattern, normalized):
                score += weight
                matched.append(pattern)
        
        if score >= 0.7:
            logger.warning(f"Injection blocked: score={score:.1f}, patterns={matched}")
            return f"Potential prompt injection detected (threat score: {score:.1f})"
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

    def sanitize_response(self, content: str) -> str:
        """Filters and validates the LLM response. Returns '[ERROR]' if guards fail."""
        if not self.enabled:
            return content
            
        # 1. Language/Charset Guard
        if self.config.get("language_guard", {}).get("enabled", True):
            if not self._check_language_purity(content):
                logger.warning("Language Guard: Anomalous charset detected in response.")
                return "[SEC_ERR: RESPONSE_MALFORMED]"

        # 2. Response Injection Guard
        if self.config.get("injection_guard", {}).get("enabled", True):
            if self._check_response_injections(content):
                logger.warning("Injection Guard: Remote response contains potential malicious patterns.")
                return "[SEC_ERR: THREAT_DETECTED]"

        sanitized = content
        # 3. Link Sanitizer
        blocked_domains = self.config.get("link_sanitization", {}).get("blocked_domains", [])
        for domain in blocked_domains:
            sanitized = sanitized.replace(domain, "[BLOCKED_LINK]")
            
        return sanitized

    def _check_language_purity(self, text: str) -> bool:
        """Detects if the text contains too many non-standard/unwanted characters."""
        if not text: return True
        
        # Count non-latin/non-standard characters
        weird_chars = 0
        total = len(text)
        for char in text:
            cat = unicodedata.category(char)
            # We allow: L (Letter), N (Number), P (Punctuation), Z (Separator), S (Symbol)
            # We block: C (Control, except newlines), M (Mark/Combining)
            if cat.startswith("C") and char not in "\r\n\t":
                weird_chars += 1
            elif cat.startswith("M"):
                weird_chars += 1
                
        # threshold: more than 5% or 20 characters of 'weird' stuff in a small response is suspicious
        ratio = weird_chars / total if total > 0 else 0
        return ratio < 0.05

    def _check_response_injections(self, text: str) -> bool:
        """Detects if the remote is leaking system instructions or trying to 'instruct' the proxy."""
        normalized = text.lower()
        threat_patterns = [
            r"system\s*prompt",
            r"you\s+are\s+a\s+helpful\s+assistant",
            r"<\|im_start\|>",
            r"<\|im_end\|>",
            r"\[inst\]",
            r"ignore\s+previous",
            r"human:",
            r"assistant:"
        ]
        if any(re.search(p, normalized) for p in threat_patterns):
            return True
            
        # 3. Response Entropy (Gibberish detection)
        if len(text) > 50:
            import math
            counts = {}
            for c in text: counts[c] = counts.get(c, 0) + 1
            entropy = -sum((count/len(text)) * math.log2(count/len(text)) for count in counts.values())
            # Very low entropy = repetition; Very high entropy = noise/gibberish
            if entropy < 1.0 or entropy > 7.0:
                logger.warning(f"Entropy Shield: Unusual response entropy detected ({entropy:.2f}).")
                return True
        
        return False
