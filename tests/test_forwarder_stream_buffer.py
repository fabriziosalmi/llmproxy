"""P1-2: Bounded stream buffer for the speculative analyzer.

Without the cap, a long streaming response would buffer its entire body
in RAM. This tests that:
- the rolling window stays at or below the configured cap
- total_chars still tracks every char ever appended (for token-estimation
  scaling when the provider omits usage)
- token estimation scales correctly when chunks have been evicted
"""


from proxy.forwarder import _BoundedStreamBuffer, _MAX_STREAM_BUFFER_CHARS


def test_default_cap_matches_module_constant():
    buf = _BoundedStreamBuffer()
    # Append enough to force eviction; verify the cap is the module constant.
    chunk = "X" * 1000
    for _ in range(_MAX_STREAM_BUFFER_CHARS // 1000 + 50):
        buf.append(chunk)
    assert buf.buf_chars <= _MAX_STREAM_BUFFER_CHARS + 1000  # +1 chunk slack


def test_buffer_caps_growth_under_long_stream():
    """5 MB stream into a 100 KB buffer must never exceed the cap by more
    than one chunk worth of data."""
    cap = 100_000
    buf = _BoundedStreamBuffer(max_chars=cap)
    chunk = "Y" * 10_000
    for _ in range(500):  # 5 MB total
        buf.append(chunk)
    assert buf.buf_chars <= cap + len(chunk), (
        f"buffer overshoot: buf_chars={buf.buf_chars} cap={cap}"
    )
    # Total chars must still reflect the full 5 MB.
    assert buf.total_chars == 5_000_000


def test_buffer_keeps_at_least_one_chunk():
    """Edge: a single chunk larger than the cap. The eviction loop must
    still leave one chunk so the analyzer has text to scan — better to
    overshoot the cap by one chunk than to lose all context."""
    buf = _BoundedStreamBuffer(max_chars=100)
    big = "Z" * 10_000
    buf.append(big)
    assert buf.text() == big
    assert buf.total_chars == 10_000


def test_buffer_text_returns_concatenated_window():
    buf = _BoundedStreamBuffer(max_chars=20)
    buf.append("abc")
    buf.append("defgh")
    buf.append("ij")
    # All within cap — text is exact.
    assert buf.text() == "abcdefghij"
    assert buf.total_chars == 10


def test_buffer_evicts_oldest_first_fifo():
    """Eviction must drop the FRONT of the list (oldest chunk), not the
    back — the analyzer cares about the recent suffix, not the prefix."""
    cap = 10
    buf = _BoundedStreamBuffer(max_chars=cap)
    buf.append("AAAAA")  # 5 chars, total=5
    buf.append("BBBBB")  # 5 chars, total=10
    buf.append("CCCCC")  # +5 → 15 over cap → evict "AAAAA"
    assert buf.text() == "BBBBBCCCCC"
    assert buf.buf_chars == 10
    assert buf.total_chars == 15


def test_buffer_handles_empty_append():
    buf = _BoundedStreamBuffer(max_chars=100)
    buf.append("")
    buf.append("x")
    buf.append("")
    assert buf.text() == "x"
    assert buf.total_chars == 1


def test_token_scaling_math_for_evicted_stream():
    """Document the contract used by the missing-usage fallback in the
    forwarder: when the buffer dropped chunks, scale sample tokens by
    total_chars / sample_chars."""
    cap = 1_000
    buf = _BoundedStreamBuffer(max_chars=cap)
    chunk = "x" * 500
    for _ in range(20):  # 10 KB total
        buf.append(chunk)
    sample_chars = len(buf.text())
    assert sample_chars <= cap + len(chunk)
    assert buf.total_chars == 10_000
    # Pretend the tokenizer counted N tokens in the sample. Scaling rule.
    sample_tokens = 250  # arbitrary
    scale = buf.total_chars / sample_chars
    estimated_total = int(sample_tokens * scale)
    # Should be roughly (total_chars / sample_chars) × sample_tokens.
    expected = (buf.total_chars * sample_tokens) // sample_chars
    assert abs(estimated_total - expected) <= 1
