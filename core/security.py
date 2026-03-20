import re
import logging
import unicodedata
import uuid
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)

class SecurityShield:
    """Orchestrates security checks for incoming LLM requests (injection, PII, trajectory analysis)."""
    
    def __init__(self, config: Dict[str, Any], assistant: Optional[Any] = None):
        self.config = config.get("security", {})
        self.enabled = self.config.get("enabled", True)
        self.assistant = assistant
        self.pii_vault = {}
        self.session_memory: Dict[str, List[float]] = {}
            
    async def analyze_speculative(self, prompt: str, stream_chunks: List[str], kill_event: Any):
        """Asynchronously monitors a response stream for security violations."""
        if not self.enabled: return
        
        last_length = 0
        import asyncio
        
        while not kill_event.is_set():
            current_response = "".join(stream_chunks)
            
            # 1. Periodically check for PII or Injections in the accumulated stream
            if len(current_response) > last_length + 20: # Every 20 chars
                # Check for raw PII leaking (unmasked)
                if self._check_pii_leak(current_response):
                    logger.warning("SPECULATIVE GUARDRAIL: PII LEAK DETECTED MID-STREAM!")
                    kill_event.set()
                    return

                if self._check_response_injections(current_response):
                    logger.warning("SPECULATIVE GUARDRAIL: THREAT PATTERN DETECTED MID-STREAM!")
                    kill_event.set()
                    return
                    
                # 2. After a certain length, run an AI sanity check on the prefix
                if len(current_response) > 500 and self.assistant:
                    # Run AI guard on prefix
                    is_safe = await self.inspect_response_ai(prompt, current_response)
                    if not is_safe:
                        logger.warning("SPECULATIVE GUARDRAIL: AI JUDGMENT - UNSAFE STREAM!")
                        kill_event.set()
                        return
                
                last_length = len(current_response)
            
            await asyncio.sleep(0.05) # Hyper-fast polling

    def _check_pii_leak(self, text: str) -> bool:
        """Checks for common PII patterns in text using regex."""
        import re as _re
        # Simple PII patterns: emails, phone numbers, SSN-like
        pii_patterns = [
            r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',  # Email
            r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b',  # Phone
            r'\b\d{3}-\d{2}-\d{4}\b',  # SSN
        ]
        for pattern in pii_patterns:
            if _re.search(pattern, text):
                return True
        return False

    def mask_pii(self, text: str) -> str:
        """Masks PII patterns with tokens and stores originals in vault for later demasking."""
        if not self.enabled: return text

        import re as _re
        masked = text
        pii_patterns = [
            (r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', 'EMAIL'),
            (r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b', 'PHONE'),
            (r'\b\d{3}-\d{2}-\d{4}\b', 'SSN'),
        ]
        for pattern, label in pii_patterns:
            for match in _re.finditer(pattern, masked):
                original = match.group()
                token = f"[PII_{label}_{uuid.uuid4().hex[:8]}]"
                self.pii_vault[token] = original
                masked = masked.replace(original, token, 1)
        return masked

    def demask_pii(self, text: str) -> str:
        """Restores original PII from the vault into the response."""
        if not self.enabled: return text
        
        restored_text = text
        for token, original in self.pii_vault.items():
            if token in restored_text:
                restored_text = restored_text.replace(token, original)
        return restored_text
        
    async def inspect_response_ai(self, prompt: str, response: str) -> bool:
        """Uses the local LLM to judge if the response is safe/appropriate."""
        if not self.enabled or not self.assistant:
            return True
            
        check_prompt = f"""
        Judge if the following AI response is safe, contains no PII, and follows general safety guidelines.
        Input Prompt: {prompt}
        AI Response: {response}
        
        Respond ONLY with 'SAFE' or 'UNSAFE'.
        """
        
        try:
            judgment = await self.assistant.generate(check_prompt)
            return "SAFE" in judgment.upper()
        except Exception as e:
            logger.error(f"AI Guard Error (fail-closed): {e}")
            return False  # Fail-closed: block on error rather than silently allow
        
    _MAX_TRACKED_SESSIONS = 10_000

    def check_session_trajectory(self, session_id: str, current_prompt: str) -> Optional[str]:
        """Analyzes the 'threat trajectory' of a session to detect multi-turn jailbreaks."""
        if not self.enabled: return None

        # 1. Calculate score for current prompt
        score, _ = self._calculate_threat_score(current_prompt)

        # 2. Update memory (with bounded growth)
        if session_id not in self.session_memory:
            if len(self.session_memory) >= self._MAX_TRACKED_SESSIONS:
                # Evict oldest session (FIFO)
                oldest = next(iter(self.session_memory))
                del self.session_memory[oldest]
            self.session_memory[session_id] = []
        
        self.session_memory[session_id].append(score)
        if len(self.session_memory[session_id]) > 10:
            self.session_memory[session_id].pop(0)
            
        # 3. Multi-turn Analysis (Crescent Attack detection)
        # If the sum of scores in any 3-turn window exceeds 1.5, block.
        if len(self.session_memory[session_id]) >= 3:
            recent_sum = sum(self.session_memory[session_id][-3:])
            if recent_sum > 1.5:
                logger.warning(f"SESSION BLOCK: Multi-turn threat detected for {session_id} (Sum={recent_sum:.2f})")
                return "Conversation trajectory indicates security risk (Multi-turn violation)"
        
        return None

    def _calculate_threat_score(self, prompt: str) -> (float, List[str]):
        """Internal helper to calculate injection threat score."""
        normalized = unicodedata.normalize("NFKC", prompt).lower()
        threats = [
            (r"ignore\s+(all\s+)?previous\s+instructions?", 0.9),
            (r"system\s*prompt", 0.8),
            (r"you\s+are\s+now\s+a", 0.7),
            (r"bypass\s+(all\s+)?(safety|security|filter)", 0.85),
            (r"reveal\s+(your\s+)?(secret|hidden|base)\s+instructions?", 0.9),
            (r"```\s*system", 0.85),
            (r"<\|im_start\|>", 0.95),
            (r"assistant:\s", 0.6),
        ]
        score = 0.0
        matched = []
        for pattern, weight in threats:
            if re.search(pattern, normalized):
                score += weight
                matched.append(pattern)
        return score, matched

    def inspect(self, body: Dict[str, Any], session_id: str = "default") -> Optional[str]:
        """
        Inspects the request body and session context for security violations.
        """
        if not self.enabled:
            return None
            
        # 0. Session Trajectory Check (Olimpo Feature)
        prompt = self._extract_prompt(body)
        if prompt:
            session_error = self.check_session_trajectory(session_id, prompt)
            if session_error: return session_error

        # 1. Payload Guard (Flooding/Size)
        flood_error = self._check_payload_flooding(body)
        if flood_error: return flood_error
        
        # 2. Injection Detector (Prompt/Hidden)
        prompt = self._extract_prompt(body)
        if prompt:
            injection_error_msg = self._check_injections(prompt)
            if injection_error_msg: return injection_error_msg
            
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
        """Wrapper for _calculate_threat_score to maintain API compatibility."""
        score, matched = self._calculate_threat_score(prompt)
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
        # 3. Anti-Steganography (Olimpo)
        if self.detect_steganography(sanitized):
            logger.warning("Anti-Steganography: Payload dropped due to anomalous entropy/patterns.")
            return "[SEC_ERR: STEGANOGRAPHY_PROTECTION_TRIGGERED]"

        # 4. Link Sanitizer
        blocked_domains = self.config.get("link_sanitization", {}).get("blocked_domains", [])
        for domain in blocked_domains:
            sanitized = sanitized.replace(domain, "[BLOCKED_LINK]")
            
        # 5. Bidirectional De-masking
        sanitized = self.demask_pii(sanitized)
            
        # 6. Cryptographic Watermarking (Olimpo)
        sanitized = self.apply_watermark(sanitized)
            
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
            r"reveal\s+your\s+instructions",
            r"ignore\s+all\s+previous\s+instructions",
            r"dan\s+mode",
            r"jailbreak",
            r"you\s+are\s+now\s+a",
            r"<\|im_start\|>",
            r"<\|im_end\|>",
            r"\[inst\]",
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

    async def detect_anomaly(self, prompt: str, response: str) -> bool:
        """AI-driven behavioral anomaly detection (Jailbreak/Exfiltration)."""
        if not self.enabled or not self.assistant:
            return False
            
        anomaly_prompt = f"""
        Analyze this interaction for security anomalies (jailbreaks, prompt injection, PII exfiltration).
        User: {prompt}
        Assistant: {response}
        
        Is this interaction malicious? Respond ONLY with 'YES' or 'NO'.
        """
        
        try:
            judgment = await self.assistant.generate(anomaly_prompt)
            return "YES" in judgment.upper()
        except Exception as e:
            logger.error(f"Anomaly Detection Error: {e}")
            return False

    def apply_watermark(self, text: str) -> str:
        """Injects invisible zero-width spaces to 'sign' the response origin."""
        if not text: return text
        # Simple pattern: inject U+200B after every 5th word
        words = text.split(" ")
        signed_words = []
        for i, word in enumerate(words):
            signed_words.append(word)
            if (i + 1) % 5 == 0:
                signed_words.append("\u200B") # Zero-width space
        return " ".join(signed_words).replace(" \u200B ", "\u200B")

    def detect_steganography(self, text: str) -> bool:
        """Detects entropy anomalies that suggest hidden data (e.g., Morse, binary in spaces)."""
        if not text: return False
        
        # 1. Check for unusual zero-width characters (U+200B, U+200C, etc.) 
        # that weren't added by us.
        zero_width_chars = len(re.findall(r"[\u200B-\u200F\uFEFF]", text))
        if zero_width_chars > len(text) * 0.05: # More than 5% zero-width
               logger.warning(f"STEGANOGRAPHY DETECTED: High density of zero-width characters ({zero_width_chars})")
               return True
               
        # 2. Check for whitespace patterns (Steganography often uses varying numbers of spaces)
        if "  " in text and len(re.findall(r" {2,}", text)) > 10:
            logger.warning("STEGANOGRAPHY DETECTED: Anomalous whitespace patterns.")
            return True
            
        return False
