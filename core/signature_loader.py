"""
External signature loader with atomic hot-reload.

Loads firewall signatures and semantic injection corpus from YAML files.
Falls back to hardcoded defaults if files are missing or invalid.
Hot-reloads every 30s (via config_watch_loop) when file hash changes.

Thread-safe: pointer swap under threading.Lock (nanoseconds).
Lock-free reads: property accessors return immutable references.
"""

import hashlib
import logging
import threading
from pathlib import Path
import yaml

logger = logging.getLogger("llmproxy.signatures")

_MIN_SIG_LEN = 3
_MAX_SIG_LEN = 500
_MAX_PATTERN_LEN = 200
_MAX_TOTAL_PATTERNS = 10_000


class SignatureStore:
    """Loads and hot-reloads firewall signatures from YAML files."""

    def __init__(self, signatures_path: str = "data/signatures.yaml",
                 corpus_path: str = "data/injection_corpus.yaml"):
        self._sig_path = Path(signatures_path)
        self._corpus_path = Path(corpus_path)
        self._lock = threading.Lock()

        # Active state (read via properties — lock-free)
        self._banned: list[bytes] = []
        self._rot13: list[bytes] = []
        self._corpus: list[tuple[str, str]] = []
        self._loaded = False

        # File hashes for change detection
        self._sig_hash: str = ""
        self._corpus_hash: str = ""

    @property
    def banned_signatures(self) -> list[bytes]:
        return self._banned

    @property
    def rot13_signatures(self) -> list[bytes]:
        return self._rot13

    @property
    def corpus(self) -> list[tuple[str, str]]:
        return self._corpus

    @property
    def loaded(self) -> bool:
        return self._loaded

    def load(self) -> bool:
        """Load signatures from YAML files. Returns True if loaded successfully."""
        new_banned, new_rot13 = self._load_signatures()
        new_corpus = self._load_corpus()

        if not new_banned and not new_corpus:
            return False

        # Atomic swap
        with self._lock:
            if new_banned:
                self._banned = new_banned
                self._rot13 = new_rot13
            if new_corpus:
                self._corpus = new_corpus
                # Invalidate semantic analyzer cache
                try:
                    from core.semantic_analyzer import set_corpus
                    set_corpus(new_corpus)
                except ImportError:
                    pass
            self._loaded = bool(new_banned) or bool(new_corpus)

        count_sigs = len(self._banned)
        count_corpus = len(self._corpus)
        logger.info(f"Signatures loaded: {count_sigs} firewall + {count_corpus} semantic patterns")
        return True

    def reload_if_changed(self) -> bool:
        """Check file hashes and reload if changed. Returns True if reloaded."""
        sig_hash = self._file_hash(self._sig_path)
        corpus_hash = self._file_hash(self._corpus_path)

        if sig_hash == self._sig_hash and corpus_hash == self._corpus_hash:
            return False

        return self.load()

    def _load_signatures(self) -> tuple[list[bytes], list[bytes]]:
        """Load banned + ROT13 signatures from YAML."""
        if not self._sig_path.exists():
            logger.debug(f"Signatures file not found: {self._sig_path}")
            return [], []

        try:
            raw = self._sig_path.read_text(encoding="utf-8")
            data = yaml.safe_load(raw)
            if not isinstance(data, dict):
                logger.warning(f"Invalid signatures YAML: expected dict, got {type(data)}")
                return [], []

            banned = []
            for sig in data.get("banned_signatures", []):
                if not isinstance(sig, str):
                    continue
                sig = sig.strip()
                if len(sig) < _MIN_SIG_LEN or len(sig) > _MAX_SIG_LEN:
                    logger.warning(f"Skipping signature (length {len(sig)}): {sig[:50]}")
                    continue
                banned.append(sig.encode("utf-8"))

            rot13 = []
            for sig in data.get("rot13_signatures", []):
                if not isinstance(sig, str):
                    continue
                sig = sig.strip()
                if len(sig) >= _MIN_SIG_LEN:
                    rot13.append(sig.encode("utf-8"))

            if len(banned) > _MAX_TOTAL_PATTERNS:
                logger.warning(f"Too many signatures ({len(banned)}), truncating to {_MAX_TOTAL_PATTERNS}")
                banned = banned[:_MAX_TOTAL_PATTERNS]

            self._sig_hash = self._file_hash(self._sig_path)
            return banned, rot13

        except Exception as e:
            logger.warning(f"Failed to load signatures from {self._sig_path}: {e}")
            return [], []

    def _load_corpus(self) -> list[tuple[str, str]]:
        """Load injection corpus from YAML."""
        if not self._corpus_path.exists():
            logger.debug(f"Corpus file not found: {self._corpus_path}")
            return []

        try:
            raw = self._corpus_path.read_text(encoding="utf-8")
            data = yaml.safe_load(raw)
            if not isinstance(data, dict):
                logger.warning(f"Invalid corpus YAML: expected dict, got {type(data)}")
                return []

            corpus = []
            for entry in data.get("patterns", []):
                if not isinstance(entry, dict):
                    continue
                pattern = str(entry.get("pattern", "")).strip()
                category = str(entry.get("category", "")).strip()
                if not pattern or not category:
                    continue
                if len(pattern) < _MIN_SIG_LEN or len(pattern) > _MAX_PATTERN_LEN:
                    continue
                corpus.append((pattern, category))

            if len(corpus) > _MAX_TOTAL_PATTERNS:
                corpus = corpus[:_MAX_TOTAL_PATTERNS]

            self._corpus_hash = self._file_hash(self._corpus_path)
            return corpus

        except Exception as e:
            logger.warning(f"Failed to load corpus from {self._corpus_path}: {e}")
            return []

    @staticmethod
    def _file_hash(path: Path) -> str:
        if not path.exists():
            return ""
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        except OSError:
            return ""
