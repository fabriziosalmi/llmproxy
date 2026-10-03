"""Error bodies for the OpenAI-compatible routes.

An OpenAI client reads ``error.message``, ``error.type`` and ``error.code`` from
a failed response. The data-plane routes raise HTTPException(detail="..."), which
FastAPI renders as ``{"detail": "..."}``: the status code was right and the body
had no ``error`` key, so every client saw an opaque failure. Nothing documented
the shape, so it was also free to change.

On ``/v1/`` the body is now the OpenAI envelope *and* keeps ``detail``, so the
bundled UI and any caller that already reads ``detail`` are unaffected. Every
other path (the ``/api/v1/`` control plane) keeps FastAPI's default response.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exception_handlers import (
    http_exception_handler as _default_http_exception_handler,
)
from fastapi.exception_handlers import (
    request_validation_exception_handler as _default_validation_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

#: Path prefix of the OpenAI-compatible data plane.
DATA_PLANE_PREFIX = "/v1/"

# status -> (error.type, error.code). Types follow OpenAI's vocabulary; codes are
# stable slugs a client can switch on.
_BY_STATUS: dict[int, tuple[str, str]] = {
    400: ("invalid_request_error", "invalid_request"),
    401: ("authentication_error", "invalid_api_key"),
    402: ("insufficient_quota", "budget_exceeded"),
    403: ("permission_error", "forbidden"),
    404: ("invalid_request_error", "not_found"),
    413: ("invalid_request_error", "payload_too_large"),
    422: ("invalid_request_error", "invalid_request"),
    429: ("rate_limit_error", "rate_limited"),
    502: ("server_error", "bad_gateway"),
    503: ("server_error", "service_unavailable"),
    504: ("server_error", "gateway_timeout"),
}


def error_body(
    message: str, status: int, *, param: str | None = None, detail: Any = None
) -> dict[str, Any]:
    """The OpenAI error envelope, plus ``detail`` for existing callers."""
    etype, code = _BY_STATUS.get(
        status, ("server_error", "internal_error") if status >= 500 else ("invalid_request_error", "error")
    )
    return {
        "error": {"message": message, "type": etype, "param": param, "code": code},
        "detail": message if detail is None else detail,
    }


def _message(detail: Any) -> str:
    if isinstance(detail, str):
        return detail
    try:
        return json.dumps(detail, default=str)
    except (TypeError, ValueError):
        return str(detail)


def _is_data_plane(request: Request) -> bool:
    return request.url.path.startswith(DATA_PLANE_PREFIX)


async def _http_exception_handler(request: Request, exc: Exception):
    # Starlette types handlers as taking Exception; this one is only registered
    # for HTTPException.
    assert isinstance(exc, StarletteHTTPException)  # nosec B101
    if not _is_data_plane(request):
        return await _default_http_exception_handler(request, exc)
    return JSONResponse(
        error_body(_message(exc.detail), exc.status_code, detail=exc.detail),
        status_code=exc.status_code,
        headers=getattr(exc, "headers", None),
    )


async def _validation_exception_handler(request: Request, exc: Exception):
    assert isinstance(exc, RequestValidationError)  # nosec B101
    if not _is_data_plane(request):
        return await _default_validation_handler(request, exc)
    errors = exc.errors()
    first = errors[0] if errors else {}
    # Drop the leading "body"/"query" source so param names the field itself.
    loc = [str(p) for p in first.get("loc", ())][1:]
    message = "; ".join(
        f"{'.'.join(str(p) for p in e.get('loc', ())[1:]) or 'request'}: {e.get('msg', 'invalid')}"
        for e in errors[:5]
    ) or "Invalid request"
    from fastapi.encoders import jsonable_encoder

    return JSONResponse(
        error_body(
            message, 422, param=".".join(loc) or None, detail=jsonable_encoder(errors)
        ),
        status_code=422,
    )


def install_error_handlers(app: FastAPI) -> None:
    """Register the data-plane error shape on ``app``."""
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)
