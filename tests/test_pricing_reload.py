import yaml
import pytest
from core.signature_loader import SignatureStore
from core.pricing import get_pricing, estimate_cost


@pytest.fixture(autouse=True)
def backup_restore_pricing():
    """Backup and restore global model pricing table to prevent test pollution."""
    import core.pricing

    original_pricing = dict(core.pricing.MODEL_PRICING)
    original_prefixes = list(core.pricing._SORTED_PREFIXES)
    yield
    core.pricing.MODEL_PRICING.clear()
    core.pricing.MODEL_PRICING.update(original_pricing)
    core.pricing._SORTED_PREFIXES[:] = original_prefixes


@pytest.fixture
def temp_pricing_file(tmp_path):
    """Creates a temporary pricing.yaml file."""
    pricing_file = tmp_path / "pricing.yaml"
    initial_pricing = {
        "pricing": {
            "gpt-4o": {"input": 2.50, "output": 10.00},
            "custom-model": {"input": 1.00, "output": 5.00},
        }
    }
    pricing_file.write_text(yaml.dump(initial_pricing), encoding="utf-8")
    return pricing_file


def test_pricing_loading_and_reloading(temp_pricing_file):
    # Initialize SignatureStore with the temp file
    # We can pass dummy files for signatures and corpus or use empty paths
    store = SignatureStore(
        signatures_path="data/signatures.yaml",
        corpus_path="data/injection_corpus.yaml",
        pricing_path=str(temp_pricing_file),
    )

    # Clean load
    assert store.load() is True

    # Verify loaded values
    assert "custom-model" in store.pricing
    assert store.pricing["custom-model"]["input"] == 1.00
    assert store.pricing["custom-model"]["output"] == 5.00

    # Check that core.pricing module's get_pricing reflects this
    pricing_info = get_pricing("custom-model")
    assert pricing_info["input"] == 1.00
    assert pricing_info["output"] == 5.00

    # Test estimate_cost
    cost = estimate_cost(
        "custom-model", prompt_tokens=1_000_000, completion_tokens=1_000_000
    )
    assert cost == 6.00  # 1.00 + 5.00

    # Modify the temp pricing file
    updated_pricing = {
        "pricing": {
            "gpt-4o": {"input": 3.00, "output": 12.00},
            "custom-model": {"input": 2.00, "output": 8.00},
        }
    }
    temp_pricing_file.write_text(yaml.dump(updated_pricing), encoding="utf-8")

    # Reload the store
    assert store.reload_if_changed() is True

    # Verify updated values in the store
    assert store.pricing["custom-model"]["input"] == 2.00
    assert store.pricing["custom-model"]["output"] == 8.00

    # Verify updated values in core.pricing
    pricing_info_updated = get_pricing("custom-model")
    assert pricing_info_updated["input"] == 2.00
    assert pricing_info_updated["output"] == 8.00

    # Verify updated cost estimation
    cost_updated = estimate_cost(
        "custom-model", prompt_tokens=1_000_000, completion_tokens=1_000_000
    )
    assert cost_updated == 10.00  # 2.00 + 8.00

    # Calling reload again without changes should return False
    assert store.reload_if_changed() is False
