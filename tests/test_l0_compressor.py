"""Tests for the l0_compressor plugin — a deterministic, ML-free context
compressor ported from l0-cache. The most important guarantee is *prose safety*:
natural-language content must never be damaged by the lossy filters."""
import importlib.util
import os

import pytest

_PATH = os.path.join(
    os.path.dirname(__file__), "..", "plugins", "installed", "l0_compressor.py"
)
_spec = importlib.util.spec_from_file_location("l0_compressor", _PATH)
l0 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(l0)

compress = l0._compress_text


def _f(text, mode="auto"):
    return compress(text, mode, 30, 30, 120, 100)


# ── Prose safety ─────────────────────────────────────────────────────────────
def test_prose_with_repeated_leading_words_is_untouched():
    prose = "\n".join(
        ["The quick brown fox jumps.", "The lazy dog sleeps.", "A different line."] * 6
    )
    assert _f(prose) == prose
    assert "(×" not in _f(prose)


def test_prose_not_classified_as_logs():
    prose = "\n".join(["I think therefore I am."] * 3 + ["Cogito ergo sum, roughly."] * 3)
    assert l0._looks_like_logs(prose.split("\n")) is False


def test_safe_mode_never_prefix_collapses_even_on_logs():
    logs = "\n".join(f"[INFO] step {i} starting now" for i in range(30))
    # safe mode applies only lossless filters → distinct lines survive
    assert _f(logs, mode="safe").count("\n") + 1 == 30


# ── Log compression ──────────────────────────────────────────────────────────
def test_logs_are_detected_and_collapsed():
    logs = "\n".join(f"[INFO] processing item {i}" for i in range(40))
    assert l0._looks_like_logs(logs.split("\n")) is True
    out = _f(logs)
    assert out.count("\n") + 1 < 40
    assert "(×" in out


def test_consecutive_identical_lines_collapse_in_safe_mode():
    text = "\n".join(["exactly the same"] * 12)
    out = _f(text, mode="safe")
    assert out == "exactly the same (×12)"


# ── Diff-aware collapse ──────────────────────────────────────────────────────
def test_unified_diff_context_is_collapsed():
    diff = (
        "@@ -1,40 +1,40 @@\n"
        + "\n".join(f" context line {i}" for i in range(15))
        + "\n-removed line\n+added line\n"
        + "\n".join(f" context line {i}" for i in range(15))
    )
    out = _f(diff, mode="safe")
    assert "unchanged diff lines" in out
    assert "-removed line" in out and "+added line" in out  # changes preserved


def test_non_diff_indented_text_is_not_diff_collapsed():
    # Indented prose (leading spaces) without a hunk header must be left alone.
    text = "\n".join(f"    indented prose line number {i}" for i in range(20))
    out = _f(text, mode="safe")
    assert "unchanged diff lines" not in out


# ── ANSI + truncation ────────────────────────────────────────────────────────
def test_ansi_is_stripped():
    assert _f("\x1b[31mred\x1b[0m text\nplain line", mode="safe").startswith("red text")


def test_head_tail_truncation_on_large_distinct_block():
    # All lines distinct first words → no prefix collapse → head/tail triggers.
    text = "\n".join(f"{chr(97 + i % 26)}{i} unique-ish entry" for i in range(400))
    out = _f(text, mode="aggressive")
    assert "lines omitted by l0_compressor" in out
    assert out.count("\n") + 1 <= 30 + 30 + 1


# ── Min-lines gate (skips short content) ─────────────────────────────────────
@pytest.mark.asyncio
async def test_plugin_skips_short_content():
    from core.plugin_engine import PluginContext

    plugin = l0.L0Compressor(config={"min_lines": 40})
    ctx = PluginContext(body={"messages": [{"role": "user", "content": "hi\nthere"}]})
    resp = await plugin.execute(ctx)
    assert ctx.body["messages"][0]["content"] == "hi\nthere"
    assert resp.action == "passthrough"
