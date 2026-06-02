import pytest
from core.cache import CacheBackend


@pytest.fixture
def semantic_cache_backend(tmp_path):
    """Create a CacheBackend with semantic caching enabled."""
    db_path = str(tmp_path / "test_semantic_cache.db")
    config = {"semantic_cache": {"enabled": True, "threshold": 0.75}}
    return CacheBackend(db_path=db_path, ttl=3600, enabled=True, config=config)


@pytest.fixture
def sample_body_france():
    return {
        "model": "gpt-4o",
        "temperature": 0.7,
        "messages": [
            {"role": "user", "content": "What is the capital of France?"},
        ],
    }


@pytest.fixture
def sample_body_france_variant():
    return {
        "model": "gpt-4o",
        "temperature": 0.7,
        "messages": [
            {"role": "user", "content": "What is capital of France?"},
        ],
    }


@pytest.fixture
def sample_body_different():
    return {
        "model": "gpt-4o",
        "temperature": 0.7,
        "messages": [
            {"role": "user", "content": "Tell me a joke about computer science"},
        ],
    }


@pytest.fixture
def sample_response():
    return {
        "id": "chatcmpl-sem123",
        "object": "chat.completion",
        "model": "gpt-4o",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "The capital of France is Paris.",
                },
                "finish_reason": "stop",
            }
        ],
    }


@pytest.mark.asyncio
async def test_semantic_cache_hit_and_miss(
    semantic_cache_backend,
    sample_body_france,
    sample_body_france_variant,
    sample_body_different,
    sample_response,
):
    """Verify exact match and semantic match caching."""
    await semantic_cache_backend.init()

    # Initially, cache should miss
    res1 = await semantic_cache_backend.get(sample_body_france, tenant_id="tenant1")
    assert res1 is None

    # Write response to cache
    await semantic_cache_backend.put(
        sample_body_france, sample_response, tenant_id="tenant1", model="gpt-4o"
    )

    # Exact match should hit
    res_exact = await semantic_cache_backend.get(
        sample_body_france, tenant_id="tenant1"
    )
    assert res_exact is not None
    assert res_exact["id"] == "chatcmpl-sem123"

    # Semantic match (slight query variant) should hit
    res_semantic = await semantic_cache_backend.get(
        sample_body_france_variant, tenant_id="tenant1"
    )
    assert res_semantic is not None
    assert res_semantic["id"] == "chatcmpl-sem123"

    # Different prompt should miss
    res_diff = await semantic_cache_backend.get(
        sample_body_different, tenant_id="tenant1"
    )
    assert res_diff is None

    # Verify cache stats
    stats = await semantic_cache_backend.stats()
    assert stats["entries"] == 1
    assert stats["hits"] == 2  # 1 exact hit + 1 semantic hit
    assert stats["misses"] == 2  # 1 initial miss + 1 different miss

    await semantic_cache_backend.close()
