"""Problem responses and per-request correlation context.

Two cross-cutting concerns live here so that no handler has to remember them:

* an error raised anywhere in a B4 endpoint becomes an RFC 9457 `application/problem+json`
  body with the declared status (never a stack trace, SQL fragment or internal path); and
* every request binds `request_id` / `correlation_id` for the structured logger, and
  clears them afterwards, so a log line can be joined to MAIN's and to the other modules'
  without the identifiers leaking into the next request on the same worker.
"""

from __future__ import annotations

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from ..errors import ProblemError
from ..logging import (
    bind_context,
    current_correlation_id,
    current_request_id,
    request_identifiers,
    reset_context,
)

PROBLEM_CONTENT_TYPE = "application/problem+json"


def problem_response(error: ProblemError, request: Request) -> JSONResponse:
    return JSONResponse(
        status_code=error.status,
        content=error.as_document(instance=request.url.path),
        media_type=PROBLEM_CONTENT_TYPE,
    )


def bind_request_context(request: Request) -> object:
    """Resolve the identifiers and bind them for the duration of the request."""
    request_id, correlation_id = request_identifiers(
        request.headers.get("traceparent"), request.headers.get("X-Request-ID")
    )
    request.state.request_id = request_id
    request.state.correlation_id = str(correlation_id)
    return bind_context(request_id=request_id, correlation_id=str(correlation_id))


class ProblemRoute(APIRoute):
    """Route that renders `ProblemError` as problem+json and binds the log context."""

    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            tokens = bind_request_context(request)
            try:
                return await original(request)
            except ProblemError as error:
                return problem_response(error, request)
            finally:
                reset_context(tokens)

        return handler


def current_identifiers() -> tuple[str | None, str | None]:
    """Identifiers for the in-flight request, for services that write audit rows."""
    return current_request_id(), current_correlation_id()
