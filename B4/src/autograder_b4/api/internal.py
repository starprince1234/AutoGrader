"""Internal service-to-service endpoints (called by B2's mail intake, never by a browser).

Why the service token is a dependency instead of a check inside the handler: a handler that
forgets a check is a hole that no signature change reveals, while a missing dependency changes
the signature. Denials are therefore uniform, and there is exactly one place to audit.

The one endpoint here answers "which account has this address". That is a question an
enumeration attack wants to ask, which is why it is unreachable without the pre-shared
credential and why it returns no address in the response.
"""

from __future__ import annotations

import hmac
import logging

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import Connection, func, select

from ..api.dependencies import B4State, Dependencies
from ..api.problem import ProblemRoute
from ..db.tables import roles as roles_table
from ..db.tables import users
from ..errors import not_found, unauthenticated
from ..registration import normalize_email

logger = logging.getLogger("autograder_b4.api.internal")


def _platform_roles(connection: Connection, user_id) -> list[str]:
    rows = connection.execute(
        select(roles_table.c.role).where(
            roles_table.c.user_id == user_id, roles_table.c.course_id.is_(None)
        )
    ).fetchall()
    return [row[0] for row in rows]


def build_internal_router(state: B4State, dependencies: Dependencies) -> APIRouter:
    router = APIRouter(route_class=ProblemRoute)

    def require_service_token(request: Request) -> None:
        """Pre-shared bearer credential; compared in constant time, never echoed."""
        expected = state.settings.internal_service_token
        header = request.headers.get("authorization", "")
        presented = header[7:].strip() if header.lower().startswith("bearer ") else ""
        if not expected or not presented or not hmac.compare_digest(presented, expected):
            raise unauthenticated("A service token is required for internal endpoints.")

    @router.get("/api/v1/internal/identities/by-email")
    def lookup_identity_by_email(
        email: str = Query(min_length=3, max_length=320),
        _caller: None = Depends(require_service_token),
        connection: Connection = Depends(dependencies.connection),
    ) -> dict[str, object]:
        normalized = normalize_email(email)
        row = connection.execute(
            select(users).where(func.lower(users.c.email) == normalized)
        ).one_or_none()
        if row is None:
            raise not_found("No account is registered with that address.")
        return {
            "user_id": str(row.user_id),
            "username": row.username,
            "display_name": row.display_name,
            "student_number": row.student_number,
            "roles": sorted(_platform_roles(connection, row.user_id)),
            "is_active": bool(row.is_active),
        }

    return router
