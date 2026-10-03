"""An endpoint's provider survives a store round trip.

The endpoints table has no provider column; the provider is persisted only as
metadata["provider"]. LLMEndpoint.provider used to be an independent field
defaulting to "openai" that neither store wrote or loaded, so a stored
Anthropic endpoint reloaded as "openai" and the forwarder, which picks its
adapter from that attribute, served it with the OpenAI adapter.
"""

import pytest

from models import EndpointStatus, LLMEndpoint
from proxy.adapters.registry import get_adapter
from proxy.forwarder import _endpoint_provider
from store.sql_store import SQLiteStore


@pytest.fixture
async def store(tmp_path):
    s = SQLiteStore(str(tmp_path / "endpoints.db"))
    await s.init_db()
    yield s
    await s.close()


def _endpoint(**kwargs) -> LLMEndpoint:
    return LLMEndpoint(
        id="ep1",
        url="https://api.anthropic.com/v1",
        status=EndpointStatus.VERIFIED,
        **kwargs,
    )


async def test_provider_in_metadata_survives_reload(store):
    await store.add_endpoint(
        _endpoint(metadata={"provider": "anthropic", "models": ["claude-x"]})
    )

    (loaded,) = await store.get_pool()

    assert loaded.metadata["provider"] == "anthropic"
    assert loaded.provider == "anthropic"


async def test_explicit_provider_argument_is_persisted(store):
    await store.add_endpoint(_endpoint(provider="google"))

    (loaded,) = await store.get_all()

    assert loaded.provider == "google"
    assert loaded.metadata["provider"] == "google"


def test_legacy_provider_type_argument_still_names_the_provider():
    assert _endpoint(provider_type="azure").provider == "azure"


def test_metadata_wins_over_a_constructor_argument():
    ep = _endpoint(provider="openai", metadata={"provider": "anthropic"})
    assert ep.provider == "anthropic"


def test_provider_defaults_to_openai_when_nothing_names_one():
    ep = _endpoint()
    assert ep.provider == "openai"
    assert "provider" not in ep.metadata


def test_constructing_does_not_mutate_the_callers_metadata():
    metadata = {"models": ["m"]}
    _endpoint(provider="groq", metadata=metadata)
    assert metadata == {"models": ["m"]}


async def test_forwarder_picks_the_anthropic_adapter_for_a_stored_endpoint(store):
    await store.add_endpoint(
        _endpoint(metadata={"provider": "anthropic", "models": ["claude-x"]})
    )
    (loaded,) = await store.get_pool()

    adapter = get_adapter(_endpoint_provider(loaded), "claude-x")

    assert adapter.provider_name == "anthropic"
    # The wrong answer this used to give.
    assert adapter.provider_name != get_adapter("openai", "claude-x").provider_name


def test_endpoint_provider_reads_fallback_chain_stand_ins():
    from types import SimpleNamespace

    assert _endpoint_provider(SimpleNamespace(provider="google")) == "google"
    assert _endpoint_provider(SimpleNamespace(provider=None, provider_type="azure")) == "azure"
    assert _endpoint_provider(None) is None
