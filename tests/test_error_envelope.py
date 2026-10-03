"""Failures on /v1 carry the OpenAI error envelope; the control plane is unchanged.

An OpenAI client reads error.message / error.type / error.code. The routes raise
HTTPException(detail=...), which FastAPI rendered as {"detail": ...}: right
status, no `error` key. On /v1 the body now has both the envelope and `detail`
(so existing callers keep working); everywhere else FastAPI's default stands.
"""

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from proxy.error_envelope import error_body, install_error_handlers
from tests.test_data_plane_auth_parity import ROUTES, _agent, _config


class Body(BaseModel):
    model: str
    n: int


def _app():
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/v1/boom")
    async def boom(status: int = 401, detail: str = "nope"):
        raise HTTPException(status_code=status, detail=detail)

    @app.get("/v1/structured")
    async def structured():
        raise HTTPException(status_code=403, detail={"code": "confirm_required", "n": 1})

    @app.get("/v1/retry")
    async def retry():
        raise HTTPException(
            status_code=503, detail="busy", headers={"Retry-After": "7"}
        )

    @app.post("/v1/typed")
    async def typed(body: Body):
        return body

    @app.get("/api/v1/boom")
    async def control_plane():
        raise HTTPException(status_code=401, detail="Unauthorized")

    @app.post("/api/v1/typed")
    async def control_typed(body: Body):
        return body

    return app


async def _call(method, path, **kwargs):
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t") as c:
        return await c.request(method, path, **kwargs)


@pytest.mark.parametrize(
    "status,etype,code",
    [
        (400, "invalid_request_error", "invalid_request"),
        (401, "authentication_error", "invalid_api_key"),
        (402, "insufficient_quota", "budget_exceeded"),
        (403, "permission_error", "forbidden"),
        (404, "invalid_request_error", "not_found"),
        (413, "invalid_request_error", "payload_too_large"),
        (429, "rate_limit_error", "rate_limited"),
        (502, "server_error", "bad_gateway"),
        (503, "server_error", "service_unavailable"),
    ],
)
async def test_the_data_plane_envelope_for_each_status(status, etype, code):
    resp = await _call("GET", "/v1/boom", params={"status": status, "detail": "why"})

    assert resp.status_code == status
    error = resp.json()["error"]
    assert error == {"message": "why", "type": etype, "param": None, "code": code}


async def test_detail_is_kept_for_callers_that_already_read_it():
    resp = await _call("GET", "/v1/boom", params={"detail": "Unauthorized: Missing API key"})

    assert resp.json()["detail"] == "Unauthorized: Missing API key"
    assert resp.json()["error"]["message"] == "Unauthorized: Missing API key"


async def test_an_unmapped_status_still_gets_an_envelope():
    resp = await _call("GET", "/v1/boom", params={"status": 507})
    assert resp.json()["error"]["type"] == "server_error"
    resp = await _call("GET", "/v1/boom", params={"status": 418})
    assert resp.json()["error"]["type"] == "invalid_request_error"


async def test_a_structured_detail_is_stringified_for_the_message_and_kept_in_detail():
    resp = await _call("GET", "/v1/structured")

    assert resp.json()["detail"] == {"code": "confirm_required", "n": 1}
    assert "confirm_required" in resp.json()["error"]["message"]


async def test_response_headers_survive():
    resp = await _call("GET", "/v1/retry")

    assert resp.status_code == 503
    assert resp.headers["Retry-After"] == "7"


async def test_a_validation_error_on_the_data_plane_names_the_field():
    resp = await _call("POST", "/v1/typed", json={"model": "m", "n": "many"})

    assert resp.status_code == 422
    error = resp.json()["error"]
    assert error["type"] == "invalid_request_error"
    assert error["param"] == "n"
    assert error["message"].startswith("n: ")
    assert isinstance(resp.json()["detail"], list)  # FastAPI's own list, kept


async def test_the_control_plane_keeps_fastapis_default_shape():
    resp = await _call("GET", "/api/v1/boom")
    assert resp.status_code == 401
    assert resp.json() == {"detail": "Unauthorized"}

    resp = await _call("POST", "/api/v1/typed", json={"model": "m", "n": "many"})
    assert resp.status_code == 422
    assert set(resp.json()) == {"detail"}


def test_error_body_is_the_one_place_the_shape_is_defined():
    assert error_body("m", 401) == {
        "error": {
            "message": "m",
            "type": "authentication_error",
            "param": None,
            "code": "invalid_api_key",
        },
        "detail": "m",
    }


# ── the real routes ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("path,body", ROUTES)
async def test_a_missing_key_on_each_data_plane_route_is_an_openai_error(path, body):
    from fastapi import FastAPI as _F

    agent = _agent(_config())
    from proxy.routes.chat import create_router as chat
    from proxy.routes.completions import create_router as completions
    from proxy.routes.embeddings import create_router as embeddings

    app = _F()
    install_error_handlers(app)
    for make in (chat, completions, embeddings):
        app.include_router(make(agent))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post(path, json=body)

    assert resp.status_code == 401
    assert resp.json()["error"]["type"] == "authentication_error"
    assert resp.json()["error"]["message"] == "Unauthorized: Missing API key"
    assert resp.json()["detail"] == "Unauthorized: Missing API key"
