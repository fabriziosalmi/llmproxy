import pytest
from store.store import EndpointStore
from models import LLMEndpoint, EndpointStatus

@pytest.mark.asyncio
async def test_endpoint_store():
    store = EndpointStore("test_endpoints.db")
    await store.init()

    # Test adding an endpoint
    from uuid import uuid4
    endpoint_id = str(uuid4())
    endpoint = LLMEndpoint(id=endpoint_id, url="http://test.ai/v1", status=EndpointStatus.FOUND, metadata={"provider": "test"})
    await store.add_endpoint(endpoint)
    assert endpoint.id == endpoint_id

    # Test getting all endpoints
    endpoints = await store.get_all()
    assert len(endpoints) > 0
    assert any(e.id == endpoint_id for e in endpoints)

    # Test updating status
    await store.update_status(endpoint_id, EndpointStatus.VERIFIED, metadata={"latency_ms": 100})
    endpoints = await store.get_all()
    target = next(e for e in endpoints if e.id == endpoint_id)
    assert target.status == EndpointStatus.VERIFIED
    assert target.latency_ms == 100

    # Clean up
    await store.remove_endpoint(endpoint_id)
    endpoints = await store.get_all()
    assert not any(e.id == endpoint_id for e in endpoints)
