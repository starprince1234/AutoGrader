"""Internal service-to-service endpoints (never reachable from a browser).

Why the service token is a dependency instead of a check inside each handler: a handler
that forgets a check is a hole that no signature change reveals, while a missing dependency
changes the signature. Denials are therefore uniform, and there is exactly one place to
audit.

The identity and authorization capabilities live here since v0.32.0:

* **identity lookup by e-mail** -- B2's mail intake attributes an inbound message to an
  account. That is a question an enumeration attack wants to ask, which is why it is
  unreachable without the credential and why the response carries no address;
* **current authorization of one account** -- B3/B2 must decide assignment/submission access
  from the caller's *current* membership. A JWT scope is only a snapshot from issuance, so
  they ask this endpoint at request time. The response is ids only: roles, course grants and
  (course, section, class) enrollments. No name, e-mail or student number travels here.

`POST /course-targets/validate` (v0.38.0) is the third: B3 owns an assignment's
**target** (target sections and named students) but not the roster, so it asks B4 whether
those ids belong to the course *before* writing the assignment. The answer echoes the
confirmed ids sorted; nothing is copied, because a target stays a filter evaluated against
the current roster.

`POST /assignment-notifications` is called by B3 when an assignment is published; it records
who must be notified and with which status, narrowed by that same target. Delivery is
performed by the B4 sender process, so this endpoint never sends mail and never reports a
scheduled message as delivered.
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
from ..classes import authorization_for_user, validate_course_target
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
    #: The assignment's target (v0.38.0). Empty means "not narrowed on that axis": the whole
    #: course when both are empty. B4 narrows its own roster with these ids and never trusts
    #: a resolved student list from B3 -- that list would be a second roster copy that drifts.
    section_ids: tuple[uuid.UUID, ...] = ()
    student_ids: tuple[uuid.UUID, ...] = ()


class CourseTargetValidationRequest(BaseModel):
    """One assignment target to confirm against a course before B3 stores it (v0.38.0).

    Both arrays are optional and default to empty: "not narrowed on that axis" is a real
    target, and a body naming neither a section nor a student (the whole course) is valid.
    """

    model_config = ConfigDict(extra="forbid")

    course_id: uuid.UUID
    section_ids: tuple[uuid.UUID, ...] = ()
    student_ids: tuple[uuid.UUID, ...] = ()


def _reject_repeated_target_ids(values: tuple[uuid.UUID, ...], *, axis: str) -> None:
    """Refuse a repeated id instead of silently collapsing it.

    The contract declares `uniqueItems`, so duplicates are already invalid; dropping them
    silently would let a client that sent one believe it targeted something it did not.
    """
    seen: set[uuid.UUID] = set()
    repeated: set[str] = set()
    for value in values:
        if value in seen:
            repeated.add(str(value))
        seen.add(value)
    if repeated:
        raise unprocessable(
            f"The target repeats an id in {axis}: " + ", ".join(sorted(repeated)) + "."
        )


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

    @router.post("/api/v1/internal/course-targets/validate")
    def validate_course_targets(
        payload: CourseTargetValidationRequest,
        _caller: None = Depends(require_service_token),
        connection: Connection = Depends(dependencies.connection),
    ) -> dict[str, object]:
        """Confirm an assignment's target against the course's current roster.

        B3 asks this **before** it writes the assignment: sections and enrollments are B4's
        facts, so an id B3 cannot check must not end up stored as an audience. Nothing is
        copied or frozen here -- the target stays a filter, evaluated again on every read and
        on the notification hand-off -- and an entirely empty target is valid: it means the
        whole course, which is what every assignment created before the field existed has.
        """
        _reject_repeated_target_ids(payload.section_ids, axis="section_ids")
        _reject_repeated_target_ids(payload.student_ids, axis="student_ids")
        confirmed = validate_course_target(
            connection,
            course_id=payload.course_id,
            section_ids=payload.section_ids,
            student_ids=payload.student_ids,
        )
        logger.info(
            "internal.course_target_validated",
            extra={
                "event": "internal.course_target_validated",
                # Ids and counts only: no name, address or student number travels here.
                "course_id": str(payload.course_id),
                "section_count": len(payload.section_ids),
                "student_count": len(payload.student_ids),
            },
        )
        return confirmed

    @router.post("/api/v1/internal/assignment-notifications")
    def create_assignment_notifications(
        payload: AssignmentNotificationRequest,
        response: Response,
        _caller: None = Depends(require_service_token),
    ) -> dict[str, object]:
        """Materialise the recipient snapshot for one published assignment.

        The audience is the course's current active roster narrowed by the assignment's
        target (`section_ids` / `student_ids`, intersected). An id no current roster row
        matches is simply not a recipient: B3 has the target confirmed by
        `validateCourseTargets` before publishing, so refusing it again here, in a different
        vocabulary, would turn one mistake into two failures.

        Idempotent by `assignment_id`: a retry (transient failure, B3 retry, duplicate
        delivery) returns the existing summary with `200` and never creates a second
        audience -- the target is a publication-time snapshot, so a later call with a
        different target cannot rewrite who was told. A different course for an existing
        assignment id is an inconsistency and is refused with `409` instead of silently
        rewriting the campaign.
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
            section_ids=payload.section_ids,
            student_ids=payload.student_ids,
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
