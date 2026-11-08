"""Internal service-to-service endpoints (never reachable from a browser).

Why the service token is a dependency instead of a check inside each handler: a handler
that forgets a check is a hole that no signature change reveals, while a missing dependency
changes the signature. Denials are therefore uniform, and there is exactly one place to
audit.

Two capabilities live here (v0.32.0):

* **identity lookup by e-mail** -- B2's mail intake attributes an inbound message to an
  account. That is a question an enumeration attack wants to ask, which is why it is
  unreachable without the credential and why the response carries no address;
* **current authorization of one account** -- B3/B2 must decide assignment/submission access
  from the caller's *current* membership. A JWT scope is only a snapshot from issuance, so
  they ask this endpoint at request time. The response is ids only: roles, course grants and
  (course, section, class) enrollments. No name, e-mail or student number travels here.

`POST /assignment-notifications` is called by B3 when an assignment is published; it records
who must be notified and with which status. Delivery is performed by the B4 sender process,
so this endpoint never sends mail and never reports a scheduled message as delivered.
"""

from __future__ import annotations

import datetime as dt
import hmac
import logging
import uuid

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection, func, select

from ..api.dependencies import B4State, Dependencies
from ..api.problem import ProblemRoute
from ..classes import authorization_for_user
from ..db.tables import users
from ..errors import conflict, not_found, unauthenticated, unprocessable
from ..notifications import materialise
from ..registration import normalize_email

logger = logging.getLogger("autograder_b4.api.internal")


class AssignmentNotificationRequest(BaseModel):
    """What B3 hands over once an assignment is published (idempotent by assignment id)."""

    model_config = ConfigDict(extra="forbid")

    assignment_id: uuid.UUID
    course_id: uuid.UUID
    title: str = Field(min_length=1, max_length=200)
    due_at: dt.datetime | None = None
    published_at: dt.datetime


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

    @router.get("/api/v1/internal/users/{user_id}/authorization")
    def get_user_authorization(
        user_id: uuid.UUID,
        _caller: None = Depends(require_service_token),
        connection: Connection = Depends(dependencies.connection),
    ) -> dict[str, object]:
        """Answer "what is this account allowed to reach, right now".

        B3 uses it to decide assignment reads, B2 to admit or refuse a submission. It reads
        the live `b4` rows on every call: a token issued before a student was added to a
        course does not limit them, and one issued before they were removed does not extend
        access. Only ids are returned.
        """
        document = authorization_for_user(connection, user_id)
        if document is None:
            raise not_found("No account exists with that id.")
        logger.info(
            "internal.user_authorization_served",
            extra={
                "event": "internal.user_authorization_served",
                "user_id": str(user_id),
                "course_grants": len(document["course_grants"]),  # type: ignore[arg-type]
                "enrollments": len(document["enrollments"]),  # type: ignore[arg-type]
            },
        )
        return document

    @router.post("/api/v1/internal/assignment-notifications")
    def create_assignment_notifications(
        payload: AssignmentNotificationRequest,
        response: Response,
        _caller: None = Depends(require_service_token),
    ) -> dict[str, object]:
        """Materialise the recipient snapshot for one published assignment.

        Idempotent by `assignment_id`: a retry (transient failure, B3 retry, duplicate
        delivery) returns the existing summary with `200` and never creates a second
        audience. A different course for an existing assignment id is an inconsistency and
        is refused with `409` instead of silently rewriting the campaign.
        """
        if payload.published_at.tzinfo is None:
            # `materialise` re-checks this as `422`; the early branch keeps the failure next
            # to the request instead of inside the transaction.
            raise unprocessable("published_at must carry a timezone.")
        existing_course = _existing_campaign_course(state, payload.assignment_id)
        if existing_course is not None and existing_course != payload.course_id:
            raise conflict(
                "This assignment already has a notification for a different course."
            )
        result = materialise(
            state.engine,
            assignment_id=payload.assignment_id,
            course_id=payload.course_id,
            title=payload.title,
            due_at=payload.due_at,
            published_at=payload.published_at,
            now=_now(),
        )
        response.status_code = 201 if result.created else 200
        logger.info(
            "internal.assignment_notification_materialised",
            extra={
                "event": "internal.assignment_notification_materialised",
                "assignment_id": str(payload.assignment_id),
                # `created` is a reserved LogRecord attribute; the field is named explicitly.
                "was_created": result.created,
                "recipients": result.summary["recipient_total"],
            },
        )
        return result.summary

    return router


def _existing_campaign_course(state: B4State, assignment_id: uuid.UUID) -> uuid.UUID | None:
    from ..db.tables import assignment_notifications as campaigns

    with state.engine.connect() as connection:
        return connection.execute(
            select(campaigns.c.course_id).where(campaigns.c.assignment_id == assignment_id)
        ).scalar_one_or_none()


def _platform_roles(connection: Connection, user_id) -> list[str]:
    from ..db.tables import roles as roles_table

    rows = connection.execute(
        select(roles_table.c.role).where(
            roles_table.c.user_id == user_id, roles_table.c.course_id.is_(None)
        )
    ).fetchall()
    return [str(row[0]) for row in rows]


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(microsecond=0)
