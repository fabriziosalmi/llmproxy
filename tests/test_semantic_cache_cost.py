"""A semantic-cache miss no longer rebuilds every stored prompt's trigram set.

On each miss the candidates (up to 200 stored prompts) had their trigram sets
rebuilt, every time, and the same rows come back on the next miss: about 10 ms at
50 words, 70 ms at 400 and 260 ms at 1200 words per miss. The stored sets are
remembered, and prompts too long for similarity matching to mean anything are not
compared at all.
"""

import time

from core.cache import SEMANTIC_MAX_PROMPT_CHARS, _find_semantic_match, _stored_trigrams
from core.semantic_analyzer import _jaccard, _to_trigrams

WORDS = "the quick brown fox jumps over a lazy dog while rain falls on distant hills".split()


def _prompt(seed: int, words: int) -> str:
    return " ".join(WORDS[(seed + i * 7) % len(WORDS)] + str((seed * 31 + i) % 97) for i in range(words))


def test_the_answer_is_the_same_as_comparing_every_set_from_scratch():
    rows = [(f"response-{i}", _prompt(i, 40)) for i in range(60)]
    current = _prompt(17, 40)[:-3] + "xyz"

    best = max(
        ((_jaccard(_to_trigrams(current), _to_trigrams(p)), r) for r, p in rows),
        key=lambda x: x[0],
    )
    expected = best[1] if best[0] >= 0.85 else None

    assert _find_semantic_match(rows, current, 0.85) == expected == "response-17"


def test_repeated_misses_reuse_the_stored_sets():
    rows = [(f"r{i}", _prompt(i, 200)) for i in range(50)]
    _stored_trigrams.cache_clear()

    cold = time.perf_counter()
    _find_semantic_match(rows, _prompt(999, 200), 0.99)
    cold = time.perf_counter() - cold
    info_after_first = _stored_trigrams.cache_info()

    warm = time.perf_counter()
    _find_semantic_match(rows, _prompt(998, 200), 0.99)
    warm = time.perf_counter() - warm
    info_after_second = _stored_trigrams.cache_info()

    assert info_after_first.misses == 50
    assert info_after_second.misses == 50  # nothing rebuilt
    assert info_after_second.hits >= 50
    assert warm < cold


def test_a_prompt_too_long_for_similarity_is_not_compared():
    long = "word " * (SEMANTIC_MAX_PROMPT_CHARS // 4)
    assert len(long) > SEMANTIC_MAX_PROMPT_CHARS
    rows = [("hit", long)]

    assert _find_semantic_match(rows, long, 0.5) is None  # incoming one too long
    assert _find_semantic_match([("hit", long)], "word " * 10, 0.0) is None  # stored one too long


def test_a_prompt_within_the_limit_still_matches_itself():
    prompt = _prompt(3, 100)
    assert len(prompt) <= SEMANTIC_MAX_PROMPT_CHARS

    assert _find_semantic_match([("hit", prompt)], prompt, 0.85) == "hit"
