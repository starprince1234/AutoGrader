"""HTTP surface: `GET /api/v1/platform/conformance` and B5's Problem Details handler.

The router declares the full path, not a prefix. The host includes every module router
without a prefix (`main.create_app`) and the *prefix* lives in `module.yaml` /
`ModuleDescriptor.routes`; that split is what lets the conformance report compare declared
prefixes against contract paths instead of comparing strings of two different shapes.

B5 is read-only. Nothing on this path writes a file, a table or an event; the only side
effects are reading `MAIN/contracts/openapi/*.yaml` and reading `request.app.state`.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from autograder_platform import ModuleDescriptor

from . import auth
from .config import current_settings
from .conformance import ConformanceReport, build_report
from .errors import PROBLEM_MEDIA_TYPE, B5Error

logger = logging.getLogger("autograder_b5")

router = APIRouter(tags=["conformance"])


@router.get(
    "/api/v1/platform/conformance",
    response_model=ConformanceReport,
    summary="Report how the running composition differs from the contract",
    responses={
        401: {
            "description": (
                "Missing, malformed or unverifiable credentials, or an unreadable issuer "
                "key set. Does not reveal whether the principal exists."
            ),
            "content": {PROBLEM_MEDIA_TYPE: {}},
        }
    },
)
def conformance(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> ConformanceReport:
    auth.authenticate(current_settings(), authorization)
    descriptors: list[ModuleDescriptor] = [
        module.descriptor for module in request.app.state.modules
    ]
    application_paths = [
        route.path for route in request.app.routes if hasattr(route, "path")
    ]
    return build_report(
        current_settings().contracts_dir, descriptors, application_paths
    )


def install_error_handlers(app) -> None:
    """Translate `B5Error` into an RFC 9457 body.

    Registered through the SDK's `configure_app` hook because a FastAPI *router* cannot
    install exception handlers -- without this the 401 would surface as a bare 500, which
    is the same failure B3 hit on its first integrated start.

    Only `B5Error` is handled. B5 deliberately does not touch `RequestValidationError` or
    any other module's exception types: the last registration wins application-wide, so
    claiming a shared type here would silently change another module's error bodies.
    """

    @app.exception_handler(B5Error)
    async def _b5_error(request: Request, exc: B5Error) -> JSONResponse:
        if exc.status == 401:
            # Rejected credentials are security-relevant and are logged with the caller's
            # correlation id and nothing else -- no token, no header dump, no principal.
            logger.warning(
                "conformance.request.unauthenticated",
                extra={
                    "event": "conformance.request.unauthenticated",
                    "reason": exc.detail,
                    "traceparent": request.headers.get("traceparent", ""),
                    "path": request.url.path,
                },
            )
        return JSONResponse(
            status_code=exc.status,
            content=exc.to_problem(str(request.url.path)),
            media_type=PROBLEM_MEDIA_TYPE,
        )
