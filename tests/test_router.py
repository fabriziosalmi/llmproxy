import pytest
from core.semantic_router import SemanticRouter, TaskComplexity

@pytest.mark.asyncio
async def test_semantic_router_classification():
    router = SemanticRouter()
    
    # Test LIGHT
    assert await router.classify("summarize this text") == TaskComplexity.LIGHT
    assert await router.classify("hi there") == TaskComplexity.LIGHT
    assert await router.classify("Short prompt") == TaskComplexity.LIGHT # Heuristic: length < 50
    
    # Test HEAVY
    assert await router.classify("Debug this complex plan step by step") == TaskComplexity.HEAVY
    assert await router.classify("Analyze the architecture and solve the optimization problem") == TaskComplexity.HEAVY
    
    # Test MEDIUM & HEAVY
    assert await router.classify("Write a long and detailed story about a robot living in a futuristic city") == TaskComplexity.MEDIUM
    assert await router.classify("Implement a complex quicksort algorithm in Python for sorting large datasets") == TaskComplexity.HEAVY

def test_tier_mapping():
    router = SemanticRouter()
    assert router.get_preferred_model_tier(TaskComplexity.LIGHT) == "smol"
    assert router.get_preferred_model_tier(TaskComplexity.MEDIUM) == "mid"
    assert router.get_preferred_model_tier(TaskComplexity.HEAVY) == "large"
