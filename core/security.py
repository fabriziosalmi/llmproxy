import re
import time
import logging
import unicodedata
import uuid
from typing import Dict, List, Any, Optional

from cachetools import TTLCache as _TTLCache

logger = logging.getLogger(__name__)


def _luhn_check(number: str) -> bool:
    """Validate a credit card number using the Luhn algorithm."""
    digits = [int(d) for d in number if d.isdigit()]
    if len(digits) < 13 or len(digits) > 19:
        return False
    checksum = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


# Presidio opt-in: NLP-based PII detection when available, regex fallback otherwise
try:
    from presidio_analyzer import AnalyzerEngine, RecognizerResult  # noqa: F401
    from presidio_anonymizer import AnonymizerEngine
    from presidio_anonymizer.entities import OperatorConfig  # noqa: F401
    _PRESIDIO_AVAILABLE = True
    _presidio_analyzer = AnalyzerEngine()
    _presidio_anonymizer = AnonymizerEngine()
    logger.info("Presidio NLP engine loaded — enhanced PII detection active")
except ImportError:
    _PRESIDIO_AVAILABLE = False
    logger.info("Presidio not installed — using regex PII detection (pip install presidio-analyzer presidio-anonymizer)")

class SecurityShield:
    """Orchestrates security checks for incoming LLM requests (injection, PII, trajectory analysis)."""

    # Per-session trajectory state: {session_id: [(score, timestamp), ...]}
    # Kept separate from pii_vault to allow independent TTL policies.
    _SESSION_TTL = 3600          # Evict sessions idle for 1 hour
    _SESSION_SCORE_TTL = 300     # Only count scores from the last 5 minutes

    def __init__(self, config: Dict[str, Any], assistant: Optional[Any] = None):
        self.config = config.get("security", {})
        self.enabled = self.config.get("enabled", True)
        self.assistant = assistant

        # PII vault: maps token → original value. TTLCache prevents unbounded
        # growth in long-running deployments (tokens expire after 1 hour).
        self.pii_vault: _TTLCache = _TTLCache(maxsize=10_000, ttl=3600)

        # Session memory: {session_id: {"scores": [(score, ts), ...], "last_seen": ts}}
        # Plain dict — eviction handled in check_session_trajectory via timestamps.
        self.session_memory: Dict[str, Any] = {}

        # Cross-session threat intelligence (S1: ThreatLedger)
        from core.threat_ledger import ThreatLedger
        ledger_cfg = self.config.get("threat_ledger", {})
        self.threat_ledger: Optional[ThreatLedger] = ThreatLedger(
            max_actors=ledger_cfg.get("max_actors", 50_000),
            window_seconds=ledger_cfg.get("window_seconds", 600),
            threshold=ledger_cfg.get("threshold", 3.0),
            min_events=ledger_cfg.get("min_events", 3),
        ) if self.config.get("threat_ledger", {}).get("enabled", True) else None

    async def analyze_speculative(self, prompt: str, stream_chunks: List[str], kill_event: Any):
        """Asynchronously monitors a response stream for security violations."""
        if not self.enabled:
            return

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

    # Regex patterns used when Presidio is not available
    _REGEX_PII_PATTERNS = [
        (r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', 'EMAIL'),
        (r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b', 'PHONE'),
        (r'\b\d{3}-\d{2}-\d{4}\b', 'SSN'),
        (r'\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b', 'CREDIT_CARD'),
        (r'\b[A-Z]{2}\d{2}[\s]?[\dA-Z]{4}[\s]?\d{4}[\s]?\d{4}[\s]?\d{4}[\s]?\d{4}[\s]?\d{0,2}\b', 'IBAN'),
    ]

    # Presidio entity types to detect
    _PRESIDIO_ENTITIES = [
        "EMAIL_ADDRESS", "PHONE_NUMBER", "US_SSN", "CREDIT_CARD",
        "PERSON", "LOCATION", "IBAN_CODE", "IP_ADDRESS",
        "US_DRIVER_LICENSE", "US_PASSPORT", "DATE_TIME",
    ]

    def _check_pii_leak(self, text: str) -> bool:
        """Checks for PII in text. Uses Presidio NLP when available, regex fallback otherwise."""
        if _PRESIDIO_AVAILABLE:
            results = _presidio_analyzer.analyze(
                text=text, language="en", entities=self._PRESIDIO_ENTITIES, score_threshold=0.7
            )
            return len(results) > 0

        for pattern, _ in self._REGEX_PII_PATTERNS:
            if re.search(pattern, text):
                return True
        return False

    def mask_pii(self, text: str) -> str:
        """Masks PII with vault tokens. Uses Presidio NLP when available, regex fallback otherwise."""
        if not self.enabled:
            return text

        if _PRESIDIO_AVAILABLE:
            return self._mask_pii_presidio(text)
        return self._mask_pii_regex(text)

    def _mask_pii_presidio(self, text: str) -> str:
        """NLP-based PII masking via Presidio — detects names, addresses, IBANs, etc."""
        results = _presidio_analyzer.analyze(
            text=text, language="en", entities=self._PRESIDIO_ENTITIES, score_threshold=0.7
        )
        if not results:
            return text

        # Sort by start position descending to replace from end (preserves offsets)
        results.sort(key=lambda r: r.start, reverse=True)
        masked = text
        for result in results:
            original = text[result.start:result.end]
            label = result.entity_type.replace("_ADDRESS", "").replace("US_", "")
            token = f"[PII_{label}_{uuid.uuid4().hex[:8]}]"
            self.pii_vault[token] = original
            masked = masked[:result.start] + token + masked[result.end:]
        return masked

    def _mask_pii_regex(self, text: str) -> str:
        """Regex-based PII masking — fast fallback when Presidio is not installed."""
        masked = text
        for pattern, label in self._REGEX_PII_PATTERNS:
            for match in re.finditer(pattern, masked):
                original = match.group()
                # Luhn validation for credit card matches (reduces false positives)
                if label == "CREDIT_CARD" and not _luhn_check(original):
                    continue
                token = f"[PII_{label}_{uuid.uuid4().hex[:8]}]"
                self.pii_vault[token] = original
                masked = masked.replace(original, token, 1)
        return masked

    def demask_pii(self, text: str) -> str:
        """Restores original PII from the vault into the response."""
        if not self.enabled:
            return text
        # Snapshot items before iterating — TTLCache may evict entries during
        # iteration which would raise RuntimeError in some cachetools versions.
        vault_items = list(self.pii_vault.items())
        for token, original in vault_items:
            if token in text:
                text = text.replace(token, original)
        return text

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
        """Analyzes the 'threat trajectory' of a session to detect multi-turn jailbreaks.

        Uses time-windowed scoring: only threat scores from the last
        _SESSION_SCORE_TTL seconds count toward the crescent-attack threshold.
        Sessions idle longer than _SESSION_TTL are evicted to bound memory.
        """
        if not self.enabled:
            return None

        now = time.time()

        # 1. Time-based eviction: purge sessions idle > _SESSION_TTL
        #    Done opportunistically (every call) — cheap O(1) check per session,
        #    full scan only when a stale session is found.
        stale = [sid for sid, data in self.session_memory.items()
                 if now - data["last_seen"] > self._SESSION_TTL]
        for sid in stale:
            del self.session_memory[sid]

        # 2. Capacity guard (hard cap after eviction)
        if session_id not in self.session_memory:
            if len(self.session_memory) >= self._MAX_TRACKED_SESSIONS:
                # Evict the session that was seen least recently (LRU)
                lru = min(self.session_memory, key=lambda k: self.session_memory[k]["last_seen"])
                del self.session_memory[lru]
            self.session_memory[session_id] = {"scores": [], "last_seen": now}

        # 3. Record timestamped score for current prompt
        score, _ = self._calculate_threat_score(current_prompt)
        self.session_memory[session_id]["scores"].append((score, now))
        self.session_memory[session_id]["last_seen"] = now

        # 4. Drop scores older than _SESSION_SCORE_TTL (sliding window)
        cutoff = now - self._SESSION_SCORE_TTL
        self.session_memory[session_id]["scores"] = [
            (s, ts) for s, ts in self.session_memory[session_id]["scores"]
            if ts >= cutoff
        ]

        # 5. Crescent Attack detection: sum of last 3 scores in the window
        recent_scores = [s for s, _ in self.session_memory[session_id]["scores"][-3:]]
        if len(recent_scores) >= 3:
            recent_sum = sum(recent_scores)
            if recent_sum > 1.5:
                logger.warning(
                    f"SESSION BLOCK: Multi-turn threat for {session_id[:8]}... (sum={recent_sum:.2f})"
                )
                return "Conversation trajectory indicates security risk (Multi-turn violation)"

        return None

    def _calculate_threat_score(self, prompt: str) -> tuple[float, List[str]]:
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

    def inspect(self, body: Dict[str, Any], session_id: str = "default",
                ip: str = "", key_prefix: str = "") -> Optional[str]:
        """
        Inspects the request body and session context for security violations.

        Args:
            body: Request body (messages, model, etc.)
            session_id: Session identifier for trajectory analysis
            ip: Client IP for cross-session threat ledger
            key_prefix: API key prefix for cross-session threat ledger
        """
        if not self.enabled:
            return None

        # 0. Session Trajectory Check (Olimpo Feature)
        prompt = self._extract_prompt(body)
        if prompt:
            session_error = self.check_session_trajectory(session_id, prompt)
            if session_error:
                return session_error

            # 0b. Cross-session threat check (ThreatLedger)
            if self.threat_ledger and (ip or key_prefix):
                score, _ = self._calculate_threat_score(prompt)
                if score > 0.0:
                    ledger_error = self.threat_ledger.record(
                        ip=ip, key_prefix=key_prefix, score=score,
                    )
                    if ledger_error:
                        return ledger_error

        # 1. Payload Guard (Flooding/Size)
        flood_error = self._check_payload_flooding(body)
        if flood_error:
            return flood_error

        # 2. Injection Detector (Prompt/Hidden)
        prompt = self._extract_prompt(body)
        if prompt:
            injection_error_msg = self._check_injections(prompt)
            if injection_error_msg:
                return injection_error_msg

            # 3. Link Sanitizer
            link_error = self._check_links(prompt)
            if link_error:
                return link_error

        return None

    def _extract_prompt(self, body: Dict[str, Any]) -> str:
        messages = body.get("messages", [])
        if messages:
            return str(messages[-1].get("content", ""))
        return str(body.get("prompt", ""))

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

        # 6. Steganographic Marker (lightweight, not cryptographic — trivially removable)
        sanitized = self.apply_watermark(sanitized)

        return sanitized

    def _check_language_purity(self, text: str) -> bool:
        """Detects if the text contains too many non-standard/unwanted characters."""
        if not text:
            return True

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

        # 3. Response Entropy (repetition/flood detection)
        # Only flag extremely LOW entropy (< 1.0) which indicates byte-flooding or
        # repetition attacks. High entropy is intentionally NOT blocked: legitimate
        # code, JSON, Base64, and non-Latin scripts (CJK, Arabic, etc.) all produce
        # high Shannon entropy values and would generate false positives.
        if len(text) > 100:
            import math
            counts: dict[str, int] = {}
            for c in text:
                counts[c] = counts.get(c, 0) + 1
            entropy = -sum((count / len(text)) * math.log2(count / len(text)) for count in counts.values())
            if entropy < 1.0:
                logger.warning(f"Entropy Shield: Suspiciously low entropy detected ({entropy:.2f}) — likely repetition attack.")
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
        """Injects zero-width spaces as a steganographic marker (NOT cryptographic -- trivially removable)."""
        if not text:
            return text
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
        if not text:
            return False

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
