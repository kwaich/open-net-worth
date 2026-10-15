"""Consistent unavailable-FX responses for reads and writes."""

from fastapi import Request
from fastapi.responses import JSONResponse

from backend.core.errors import MissingFxRateError, to_http_status


async def missing_fx_response(request: Request, exc: MissingFxRateError) -> JSONResponse:
    return JSONResponse(
        status_code=to_http_status(exc),
        content={"detail": exc.message, "code": exc.code, **exc.context},
    )
