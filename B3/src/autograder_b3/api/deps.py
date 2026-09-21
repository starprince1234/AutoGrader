"""Request dependencies: dependencies container, request context, authentication.

The container is built once from :class:`~autograder_b3.config.Settings` and stored
on the FastAPI app; tests build it explicitly with a test database, a static token
verifier (standing in for B4's JWKS endpoint) and an in-memory event publisher.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, UTC
from typing import Any
from collections.abc import Callable

from fastapi import Request

from ..auth import (
    JwksTokenVerifier,
    Principal,
    SERVICE_PRINCIPAL,
    ServiceTokenVerifier,
    TokenVerifier,
    parse_bearer,
    require_authenticated,
)
from ..config import Settings
from ..errors import Unauthenticated
from ..infra.db import Database
from ..infra.publisher import EventPublisher, InMemoryEventPublisher, NatsEventPublisher


@dataclass
class B3Dependencies:
    settings: Settings
    database: Database
    token_verifier: TokenVerifier | None = None
    service_verifier: ServiceTokenVerifier | None = None
    publisher: EventPublisher | None = None
    clock: Callable[[], datetime] = field(
        default_factory=lambda: lambda: datetime.now(UTC)
    )

    @property
    def now(self) -> datetime:
        return self.clock()


def build_dependencies(
    settings: Settings | None = None,
    *,
    database: Database | None = None,
    token_verifier: TokenVerifier | None = None,
    service_verifier: ServiceTokenVerifier | None = None,
    publisher: EventPublisher | None = None,
    clock: Callable[[], datetime] | None = None,
) -> B3Dependencies:
    resolved = settings or Settings.from_env()
    if token_verifier is None and resolved.jwks_url:
        token_verifier = JwksTokenVerifier(
            jwks_url=resolved.jwks_url,
            issuer=resolved.jwt_issuer,
            audience=resolved.jwt_audience,
        )
    if publisher is None and resolved.nats_url:
        publisher = NatsEventPublisher(resolved.nats_url)
    return B3Dependencies(
        settings=resolved,
        database=database or Database(resolved),
        token_verifier=token_verifier,
        service_verifier=(
            service_verifier
            if service_verifier is not None
            else (
                ServiceTokenVerifier(resolved.service_token)
                if resolved.service_token
                else None
            )
        ),
        publisher=publisher if publisher is not None else InMemoryEventPublisher(),
        clock=clock or (lambda: datetime.now(UTC)),
    )


@dataclass(frozen=True)
class RequestContext:
    """Per-request correlation state (``MAIN/docs/api/identifiers.md`` §5)."""

    request_id: str
    traceparent: str | None
    principal: Principal | None
    path: str

    @property
    def correlation_id(self) -> uuid.UUID:
        try:
            return uuid.UUID(self.request_id)
        except (ValueError, AttributeError):
            return uuid.uuid5(uuid.NAMESPACE_URL, f"autograder-b3:{self.request_id}")


_DEFAULT_DEPENDENCIES: B3Dependencies | None = None


def set_default_dependencies(dependencies: B3Dependencies) -> None:
    """Install the container used when the host mounts B3's router into its own app.

    The composition host calls ``B3Module.router(context)`` and includes the
    returned router in the host application, so ``request.app.state`` belongs to the
    host. This fallback keeps a single container per process without requiring the
    host to know B3's internals.
    """
    global _DEFAULT_DEPENDENCIES
    _DEFAULT_DEPENDENCIES = dependencies


def get_dependencies(request: Request) -> B3Dependencies:
    dependencies = getattr(request.app.state, "b3_dependencies", None) or _DEFAULT_DEPENDENCIES
    if dependencies is None:  # pragma: no cover - wiring defect, not a user error
        raise RuntimeError("B3 dependencies are not wired onto the application")
    return dependencies


def request_context(request: Request) -> RequestContext:
    dependencies = get_dependencies(request)
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    return RequestContext(
        request_id=request_id,
        traceparent=request.headers.get("traceparent"),
        principal=resolve_principal(request, dependencies),
        path=request.url.path,
    )


def resolve_principal(request: Request, dependencies: B3Dependencies) -> Principal | None:
    token = parse_bearer(request.headers.get("Authorization"))
    if token is None:
        return None
    if dependencies.service_verifier is not None and dependencies.service_verifier.matches(token):
        return SERVICE_PRINCIPAL
    if dependencies.token_verifier is None:
        raise Unauthenticated("token verification is not configured")
    return dependencies.token_verifier.verify(token)


def current_principal(request: Request) -> Principal:
    context = request_context(request)
    return require_authenticated(context.principal)


def cookie_or_header_token(request: Request) -> str | None:
    return parse_bearer(request.headers.get("Authorization"))


def describe_dependencies(dependencies: B3Dependencies) -> dict[str, Any]:
    """Health detail: which optional capabilities are wired (no secrets)."""
    return {
        "database": bool(dependencies.database),
        "token_verifier": type(dependencies.token_verifier).__name__
        if dependencies.token_verifier
        else None,
        "service_token_configured": dependencies.service_verifier is not None,
        "publisher": type(dependencies.publisher).__name__ if dependencies.publisher else None,
    }
