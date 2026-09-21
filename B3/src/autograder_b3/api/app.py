"""FastAPI wiring: error handlers, request logging, app factory.

``create_app`` exists so tests can exercise the real HTTP surface against a real
PostgreSQL; the composition host builds the same router through
``plugin.B3Module.router(context)``.
"""

from __future__ import annotations

import time
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError

from ..errors import B3Error, UnprocessableEntity
from ..logging import get_logger, log_event, to_rfc3339_millis
from .deps import B3Dependencies, build_dependencies
from .router import router
from datetime import UTC

PROBLEM_MEDIA_TYPE = "application/problem+json"
IMMUTABLE_SQLSTATE = "23001"


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(B3Error)
    async def _b3_error(request: Request, exc: B3Error) -> JSONResponse:
        return _problem_response(request, exc)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        error = UnprocessableEntity(_summarize_validation(exc))
        return _problem_response(request, error)

    @app.exception_handler(DBAPIError)
    async def _db_error(request: Request, exc: DBAPIError) -> JSONResponse:
        sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
        if sqlstate == IMMUTABLE_SQLSTATE:
            from ..errors import ImmutableVersionConflict

            return _problem_response(
                request,
                ImmutableVersionConflict(
                    "published question versions are immutable; create a new version instead"
                ),
            )
        log_event(
            get_logger("api"),
            "request.failed",
            level=40,
            request_id=_request_id(request),
            error_type=type(exc).__name__,
            status_code=500,
        )
        return _problem_response(
            request, B3Error("the request could not be completed", headers={})
        )


def _summarize_validation(exc: RequestValidationError) -> str:
    details: list[str] = []
    for item in exc.errors()[:5]:
        location = ".".join(str(part) for part in item.get("loc", ()))
        details.append(f"{location}: {item.get('msg', 'invalid')}")
    return "request validation failed - " + "; ".join(details)


def _problem_response(request: Request, error: B3Error) -> JSONResponse:
    instance = str(request.url.path)
    body = error.to_problem(instance)
    if error.status >= 500:
        log_event(
            get_logger("api"),
            "request.failed",
            level=40,
            request_id=_request_id(request),
            reason=error.slug,
            status_code=error.status,
        )
    return JSONResponse(
        status_code=error.status,
        content=body,
        media_type=PROBLEM_MEDIA_TYPE,
        headers=error.headers,
    )


def _request_id(request: Request) -> str:
    return request.headers.get("X-Request-ID") or "-"


def install_request_logging(app: FastAPI) -> None:
    @app.middleware("http")
    async def _log_request(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        started = time.perf_counter()
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - started) * 1000, 3)
        response.headers["X-Request-ID"] = request_id
        log_event(
            get_logger("api"),
            "request.completed",
            request_id=request_id,
            status_code=response.status_code,
            duration_ms=duration_ms,
            outcome=request.method,
        )
        return response


def create_app(dependencies: B3Dependencies | None = None) -> FastAPI:
    app = FastAPI(
        title="AutoGrader B3 Question Bank API",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.b3_dependencies = dependencies or build_dependencies()
    install_error_handlers(app)
    install_request_logging(app)
    app.include_router(router)
    return app


def health_detail(dependencies: B3Dependencies) -> str:
    from ..stats.health import consumption_health
    from .deps import describe_dependencies

    detail = describe_dependencies(dependencies)
    consumption = consumption_health(dependencies.database)
    return (
        "question bank ready; "
        f"token_verifier={detail['token_verifier']} "
        f"publisher={detail['publisher']} "
        f"service_token_configured={detail['service_token_configured']}; "
        f"{consumption.describe()}"
    )


def now_millis() -> str:
    from datetime import datetime

    return to_rfc3339_millis(datetime.now(UTC))
