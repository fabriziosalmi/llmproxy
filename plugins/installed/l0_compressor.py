"""
l0 Compressor — deterministic, zero-dependency context compression.

A faithful Python port of the filter pipeline from l0-cache
(github.com/fabriziosalmi/l0-cache, MIT), adapted to run as a PRE_FLIGHT
plugin over the conversation `messages[]` instead of wrapping a shell command.

Why this exists: it is the in-house, ML-free alternative to external context
compressors. No model, no network, no `litellm` transitive dependency — just a
streaming line pipeline that strips the redundancy out of logs, build output,
stack traces and diffs that agents routinely paste into a prompt.

Pipeline (per message, in order, mirroring l0-cache's FilterPipeline):
  1. ANSI escape stripping
  2. unified-diff context collapsing  (keep edges, drop long unchanged runs)
  3. consecutive-line collapse        (identical → "line (×N)"; prefix/fuzzy
                                        runs → "prefix ... (×N)")
  4. blank-line squeeze               (collapse runs of blank lines to one)
  5. head/tail truncation             (only above a line threshold; a non-zero
                                        error signal widens the retained tail)

Prose safety: unlike l0-cache (which only ever sees command output), a chat
message can be natural language. Prefix/fuzzy collapse and head/tail truncation
are *lossy for prose*, so they are gated behind a "looks like logs" heuristic
(mode="auto", default) or an explicit mode="aggressive". mode="safe" only ever
applies the near-lossless filters (ANSI strip, exact-duplicate collapse, blank
squeeze, diff collapse), which never damage natural-language text.

Deterministic and fail-open: on any error the original message is left intact.
"""
import re
from typing import Any

from core.plugin_sdk import BasePlugin, PluginHook, PluginResponse
from core.plugin_engine import PluginContext

# ── Defaults (mirrored from l0-cache src/filter.rs) ──────────────────────────
DEFAULT_HEAD = 30
DEFAULT_TAIL = 30
DEFAULT_TAIL_ERROR = 120
DEFAULT_THRESHOLD = 100  # only truncate when filtered line count exceeds this
MIN_PREFIX_LEN = 2
DIFF_CTX_KEEP = 3        # context lines kept at each edge of a collapsed run
DIFF_CTX_MIN = 8         # only collapse unchanged runs longer than this

# Keywords that mark a line as carrying error/warning signal — shared by the
# tail-sizing gate. Mirrors l0-cache's ERROR_SIGNAL_KEYWORDS.
_ERROR_SIGNAL = ("error", "warn", "fail", "exception", "panic", "traceback", "fatal")

_ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_MONTHS = frozenset(
    ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
)


def _has_error_signal(line: str) -> bool:
    low = line.lower()
    return any(kw in low for kw in _ERROR_SIGNAL)


def _strip_ansi(text: str) -> str:
    if "\x1b" not in text:
        return text
    return _ANSI_RE.sub("", text)


def _skip_timestamp(line: str) -> str:
    """Return the line with a leading syslog/ISO timestamp removed, so prefix
    collapse keys on the meaningful first word rather than a varying time."""
    s = line.lstrip()
    # syslog: "Oct 12 10:30:00 ..."
    if len(s) >= 15 and s[0:3] in _MONTHS and (s[12:13] == ":" or s[13:14] == ":"):
        return s[15:].lstrip()
    # ISO8601 / date-time starting with a digit
    if s[:1].isdigit():
        parts = s.split(None, 2)
        t1 = parts[0] if parts else ""
        is_date = "-" in t1 or "/" in t1
        is_time = ":" in t1
        if is_date or is_time:
            if "T" in t1 and ":" in t1:  # full ISO: 2024-10-12T10:30:00Z
                return s[len(t1):].lstrip()
            if len(parts) > 1 and ":" in parts[1] and any(c.isdigit() for c in parts[1]):
                idx = s.find(parts[1]) + len(parts[1])
                return s[idx:].lstrip()
            return s[len(t1):].lstrip()
    return s


def _first_word(line: str):
    stripped = _skip_timestamp(line)
    parts = stripped.split()
    return parts[0] if parts else None


def _prefix_with_indent(line: str) -> str:
    parts = line.split()
    if not parts:
        return line
    word = parts[0]
    idx = line.find(word)
    return line[: idx + len(word)]


def _fuzzy_key(s: str) -> str:
    """First 40 chars, letters only — collapses lines differing only by IDs."""
    return "".join(c for c in s[:40] if c.isalpha())


# ── Unified-diff context collapse ────────────────────────────────────────────
def _is_hunk_header(line: str) -> bool:
    return line.startswith("@@ ") and "@@" in line[3:]


def _is_diffish(line: str) -> bool:
    return (line[:1] in ("+", "-", " ", "@", "\\")) or line.startswith(("diff ", "index "))


def _diff_collapse(lines: list) -> list:
    """Collapse long runs of unchanged context inside unified diffs. Activates
    only after a real hunk header, so non-diff text is never touched."""
    out: list = []
    ctx: list = []
    active = False

    def flush():
        n = len(ctx)
        if n == 0:
            return
        if n <= DIFF_CTX_MIN:
            out.extend(ctx)
        else:
            out.extend(ctx[:DIFF_CTX_KEEP])
            out.append(f" ... ({n - DIFF_CTX_KEEP * 2} unchanged diff lines) ...")
            out.extend(ctx[n - DIFF_CTX_KEEP:])
        ctx.clear()

    for line in lines:
        if _is_hunk_header(line):
            flush()
            active = True
            out.append(line)
            continue
        if active and line.startswith(" "):
            ctx.append(line)
            continue
        flush()
        if active and not _is_diffish(line):
            active = False
        out.append(line)
    flush()
    return out


# ── Consecutive-line collapse (identical + prefix + fuzzy) ────────────────────
def _collapse_lines(lines: list, prefix_mode: bool) -> list:
    """Collapse consecutive identical lines to "line (×N)". When prefix_mode is
    on, also collapse runs sharing a first word (or fuzzy key) to "prefix ... (×N)"."""
    out: list = []
    last = None          # previous distinct line (for identical runs)
    count = 0
    first_in_run = None  # first line of an active prefix run
    run_prefix = None

    def emit_pending():
        nonlocal last, count, first_in_run, run_prefix
        if first_in_run is not None:
            indent = first_in_run[: len(first_in_run) - len(first_in_run.lstrip())]
            prefix = f"{indent}{run_prefix}" if run_prefix else _prefix_with_indent(first_in_run)
            out.append(f"{prefix} ... (×{count})")
            first_in_run = None
            run_prefix = None
            last = None
            count = 0
        elif last is not None:
            out.append(f"{last} (×{count})" if count > 1 else last)
            last = None
            count = 0

    def same_prefix(a: str, b: str) -> bool:
        wa, wb = _first_word(a), _first_word(b)
        if wa and wb and len(wa) >= MIN_PREFIX_LEN and wa == wb:
            return True
        fa = _fuzzy_key(a)
        return len(fa) >= 10 and fa == _fuzzy_key(b)

    for line in lines:
        if last is not None and last == line:
            count += 1
            continue
        if prefix_mode and last is not None and same_prefix(last, line):
            if first_in_run is None:
                first_in_run = last
                run_prefix = _first_word(first_in_run)
            count += 1
            last = line
            continue
        emit_pending()
        last = line
        count = 1
    emit_pending()
    return out


def _squeeze_blanks(lines: list) -> list:
    out: list = []
    blanks = 0
    for line in lines:
        if line.strip() == "":
            blanks += 1
            if blanks <= 1:
                out.append(line)
        else:
            blanks = 0
            out.append(line)
    return out


def _head_tail(lines: list, head: int, tail: int, threshold: int) -> list:
    total = len(lines)
    if total <= threshold or total <= head + tail:
        return lines
    omitted = total - head - tail
    return lines[:head] + [f" ... ({omitted} lines omitted by l0_compressor) ..."] + lines[total - tail:]


# ── Log-likeness heuristic (gates the lossy filters for prose safety) ─────────
_LOGLEVEL_RE = re.compile(r"\b(DEBUG|INFO|WARN|WARNING|ERROR|TRACE|FATAL)\b")


def _looks_like_logs(lines: list) -> bool:
    """Conservative heuristic: only call content log-like when there is strong
    structural signal, so natural-language prose is never aggressively collapsed."""
    if len(lines) < 8:
        return False
    if any(_is_hunk_header(ln) for ln in lines):
        return True
    non_empty = [ln for ln in lines if ln.strip()]
    if not non_empty:
        return False
    # Only a strong *structural* signal counts. A shared first word is NOT enough:
    # ordinary prose frequently repeats sentence-leading words ("The …", "I …"),
    # which must never trigger the lossy prefix/fuzzy collapse.
    structured = sum(
        1 for ln in non_empty
        if _LOGLEVEL_RE.search(ln) or _has_error_signal(ln) or ln.lstrip()[:1] in ("[", "{", "+")
    )
    return (structured / len(non_empty)) >= 0.30


def _compress_text(text: str, mode: str, head: int, tail: int, tail_error: int,
                   threshold: int) -> str:
    lines = _strip_ansi(text).split("\n")
    if mode == "auto":
        aggressive = _looks_like_logs(lines)
    else:
        aggressive = mode == "aggressive"

    lines = _diff_collapse(lines)
    lines = _collapse_lines(lines, prefix_mode=aggressive)
    lines = _squeeze_blanks(lines)
    if aggressive:
        display_tail = tail_error if any(_has_error_signal(ln) for ln in lines) else tail
        lines = _head_tail(lines, head, display_tail, threshold)
    return "\n".join(lines)


class L0Compressor(BasePlugin):
    name = "l0_compressor"
    hook = PluginHook.PRE_FLIGHT
    version = "1.0.0"
    author = "Fabrizio Salmi (l0-cache port)"
    description = (
        "Deterministic, ML-free context compression. Strips ANSI, collapses "
        "duplicate/diff/log noise and truncates oversized blocks in messages[]. "
        "Port of l0-cache (github.com/fabriziosalmi/l0-cache). No external deps."
    )
    timeout_ms = 200  # pure-Python line processing; fast even on large blocks

    def __init__(self, config: Any = None):
        super().__init__(config)
        self._mode = (self.config.get("mode", "auto") or "auto").lower()
        self._min_lines = int(self.config.get("min_lines", 40))
        self._head = int(self.config.get("head_lines", DEFAULT_HEAD))
        self._tail = int(self.config.get("tail_lines", DEFAULT_TAIL))
        self._tail_error = int(self.config.get("tail_error_lines", DEFAULT_TAIL_ERROR))
        self._threshold = int(self.config.get("truncate_threshold", DEFAULT_THRESHOLD))
        if self._mode not in ("safe", "aggressive", "auto"):
            self._mode = "auto"

    async def execute(self, ctx: PluginContext) -> PluginResponse:
        body = ctx.body
        messages = body.get("messages")
        if not messages:
            return PluginResponse.passthrough()

        chars_before = 0
        chars_after = 0
        any_changed = False

        for msg in messages:
            content = msg.get("content")
            if not content or not isinstance(content, str):
                continue
            # Skip short content: nothing to gain, and it is usually the real prompt.
            if content.count("\n") + 1 < self._min_lines:
                continue
            try:
                compressed = _compress_text(
                    content, self._mode, self._head, self._tail,
                    self._tail_error, self._threshold,
                )
            except Exception as exc:  # fail-open: never corrupt a request
                self.logger.warning(f"l0_compressor: skipped a message ({exc})")
                continue
            if compressed != content and len(compressed) < len(content):
                chars_before += len(content)
                chars_after += len(compressed)
                msg["content"] = compressed
                any_changed = True

        if not any_changed:
            return PluginResponse.passthrough()

        saved = chars_before - chars_after
        pct = (saved / chars_before * 100) if chars_before else 0.0
        ctx.metadata["l0_compressed"] = True
        ctx.metadata["l0_chars_saved"] = saved
        self.logger.info(
            f"l0_compressor: {saved} chars saved ({chars_before} → {chars_after}, "
            f"-{pct:.0f}%)"
        )
        return PluginResponse.modify(body=body)
