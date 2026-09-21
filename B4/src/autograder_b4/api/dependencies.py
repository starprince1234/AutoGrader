"""FastAPI dependencies: state, database connections, authentication and authorization.

Authorization is a dependency rather than a check inside each handler for a reason: a
handler that forgets to call it is a hole, while a missing dependency changes the
signature of the endpoint. Denials are audited in this one place, so "every `403` leaves
a record" is a property of the wiring rather than of reviewer diligence.

Dependencies are built as closures over one :class:`B4State`. They are plain functions,
not bound methods, because FastAPI resolves a dependency by calling it with the
sub-dependencies named in its signature and would otherwise pass `self` as a query
parameter.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime

from fastapi import Depends, Request
from sqlalchemy import Connection, Engine, select

from ..audit import ACTION_AUTHORIZATION_DENIED, OUTCOME_DENIED, AuditActor, AuditLog
from ..authorization import (
    Principal,
    require_any_platform_role,
    require_course_read,
    require_course_write,
    require_platform_role,
    resolve_principal,
)
from ..config import Settings
from ..db.tables import users
from ..errors import ProblemError, unauthenticated
from ..logging import (
    bind_context,
    current_correlation_id,
    current_request_id,
    get_logger,
    log_operation,
)
from ..registry import ModuleManifest
from ..security.keys import SigningKey
from ..security.tokens import TokenError, verify_access_token

logger = get_logger("api")


@dataclass
class B4State:
    """Everything an endpoint needs, created once when the host wires the router."""

    settings: Settings
    engine: Engine
    signing_key: SigningKey
    manifests: dict[str, ModuleManifest] = field(default_factory=dict)
    #: The host's `ModuleContext`, kept so B4 can ask sibling modules for their health
    #: through the interface the host publishes rather than by reaching into them.
    context: object | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    #: The last readiness state B4 recorded, and the lock that makes "compare, then set"
    #: atomic. Readiness is polled continuously, so the probe records only state changes;
    #: without the lock two concurrent probes could both believe they saw the transition.
    _last_health: tuple[bool, str] | None = field(default=None, init=False, repr=False)
    _health_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    @property
    def module_root(self):
        return self.settings.module_root

    def self_health(self) -> tuple[bool, str]:
        """Probe PostgreSQL, the migration head and NATS; the detail names each one.

        Every probe performs a real round trip, so the answer changes when a dependency
        actually goes away (`RQ-B-4-10` acceptance 1, `RQ-B-4-20` acceptance 4).

        The result is also recorded (`RQ-B-4-99` acceptance 4) -- but **only when the state
        changes**: this is a hot read path, and one record per poll would turn monitoring
        into log write amplification without telling an operator anything new.
        """
        from ..db.engine import database_health, migration_head_status
        from ..messaging.broker import nats_reachability

        checks = [
            database_health(self.engine),
            migration_head_status(self.engine, self.settings.module_root),
        ]
        broker = nats_reachability(self.settings.nats_url)
        checks.append(type(checks[0])(name="nats", healthy=broker.healthy, detail=broker.detail))
        healthy = all(check.healthy for check in checks)
        detail = "; ".join(f"{check.name}: {check.detail}" for check in checks)
        self._record_health_state(healthy, detail)
        return healthy, detail

    def _record_health_state(self, healthy: bool, detail: str) -> None:
        with self._health_lock:
            previous = self._last_health
            if previous is not None and previous[0] == healthy:
                return
            self._last_health = (healthy, detail)
        log_operation(
            logger,
            "ops.health.changed",
            level=logging.INFO if healthy else logging.ERROR,
            healthy=healthy,
            previous_healthy=None if previous is None else previous[0],
            detail=detail,
            previous_detail=None if previous is None else previous[1],
        )


def actor_of(principal: Principal) -> AuditActor:
    return AuditActor(user_id=principal.user_id, roles=principal.all_roles)


def audit_denial(
    engine: Engine,
    *,
    principal: Principal | None,
    detail: str,
    course_id: uuid.UUID | None = None,
) -> None:
    """Record a denied authorization decision, in its own transaction."""
    actor = AuditActor(
        user_id=principal.user_id if principal else None,
        roles=principal.all_roles if principal else (),
    )
    with engine.begin() as connection:
        AuditLog(connection).record(
            action=ACTION_AUTHORIZATION_DENIED,
            outcome=OUTCOME_DENIED,
            actor=actor,
            target_type="authorization",
            course_id=course_id,
            request_id=current_request_id(),
            correlation_id=current_correlation_id(),
            detail=detail,
        )
    logger.warning(
        "authz.denied",
        extra={
            "event": "authz.denied",
            "reason": detail,
            "course_id": str(course_id) if course_id else None,
        },
    )


@dataclass(frozen=True)
class Dependencies:
    """The dependency callables a router needs, closed over one state."""

    state: B4State
    connection: Callable[..., Iterator[Connection]]
    principal: Callable[..., Principal]
    optional_principal: Callable[..., Principal | None]
    administrator: Callable[..., Principal]
    course_writer: Callable[..., Principal]
    course_reader: Callable[..., Principal]


def build_dependencies(state: B4State) -> Dependencies:
    def connection() -> Iterator[Connection]:
        with state.engine.connect() as open_connection:
            yield open_connection

    def _bearer(request: Request) -> str:
        header = request.headers.get("authorization") or ""
        scheme, _, value = header.partition(" ")
        if scheme.lower() != "bearer" or not value.strip():
            raise unauthenticated()
        return value.strip()

    def principal(
        request: Request,
        open_connection: Connection = Depends(connection),
    ) -> Principal:
        token = _bearer(request)
        try:
            claims = verify_access_token(
                token,
                signing_key=state.signing_key,
                issuer=state.settings.jwt_issuer,
                audience=state.settings.jwt_audience,
            )
        except TokenError as error:
            logger.warning(
                "auth.token_rejected",
                extra={"event": "auth.token_rejected", "reason": str(error)},
            )
            raise unauthenticated() from error
        try:
            user_id = uuid.UUID(claims.subject)
        except ValueError as error:  # pragma: no cover - our own tokens always carry a UUID
            raise unauthenticated() from error
        row = open_connection.execute(
            select(users.c.is_active).where(users.c.user_id == user_id)
        ).fetchone()
        if row is None or not row[0]:
            # A deactivated user cannot keep using an unexpired token.
            raise unauthenticated()
        bind_context(user_id=str(user_id))
        return resolve_principal(open_connection, user_id)

    def optional_principal(
        request: Request,
        open_connection: Connection = Depends(connection),
    ) -> Principal | None:
        if not request.headers.get("authorization"):
            return None
        return principal(request, open_connection)

    def administrator(caller: Principal = Depends(principal)) -> Principal:
        return _enforce(lambda: require_platform_role(caller, "admin"), caller, None)

    def course_writer(
        course_id: uuid.UUID,
        caller: Principal = Depends(principal),
    ) -> Principal:
        return _enforce(lambda: require_course_write(caller, course_id), caller, course_id)

    def course_reader(
        course_id: uuid.UUID,
        caller: Principal = Depends(principal),
    ) -> Principal:
        return _enforce(lambda: require_course_read(caller, course_id), caller, course_id)

    def _enforce(
        check: Callable[[], None], caller: Principal, course_id: uuid.UUID | None
    ) -> Principal:
        try:
            check()
        except ProblemError as error:
            audit_denial(state.engine, principal=caller, detail=error.detail, course_id=course_id)
            raise
        return caller

    return Dependencies(
        state=state,
        connection=connection,
        principal=principal,
        optional_principal=optional_principal,
        administrator=administrator,
        course_writer=course_writer,
        course_reader=course_reader,
    )


def ensure_course_read(
    *, principal: Principal, course_id: uuid.UUID, dependencies: Dependencies
) -> None:
    """Authorize a course read in a handler that has already resolved the course."""
    try:
        require_course_read(principal, course_id)
    except ProblemError as error:
        audit_denial(
            dependencies.state.engine,
            principal=principal,
            detail=error.detail,
            course_id=course_id,
        )
        raise


def ensure_course_write(
    *, principal: Principal, course_id: uuid.UUID, dependencies: Dependencies
) -> None:
    try:
        require_course_write(principal, course_id)
    except ProblemError as error:
        audit_denial(
            dependencies.state.engine,
            principal=principal,
            detail=error.detail,
            course_id=course_id,
        )
        raise


def require_operations_or_admin(principal: Principal) -> None:
    """Platform-wide read of the registry: administrators only."""
    require_any_platform_role(principal, "admin")
