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
import uuid

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection, func, select

from ..api.dependencies import B4State, Dependencies
from ..api.problem import ProblemRoute
from ..db.tables import enrollments, sections, users
from ..db.tables import roles as roles_table
from ..errors import not_found, unauthenticated
from ..registration import normalize_email

logger = logging.getLogger("autograder_b4.api.internal")

#: Upper bounds mirror `openapi/b4.yaml` (`CourseTargetValidationRequest`). They are not a
#: business limit; they keep one request from asking the database to expand an unbounded
#: `IN (...)`, which is the only resource question this read-only endpoint can raise.
MAX_TARGET_SECTIONS = 50
MAX_TARGET_STUDENTS = 2000


class CourseTargetValidationRequest(BaseModel):
    """Assignment target ids to validate against one course.

    `section_ids` may be empty only in theory (B3 always sends at least one); an empty list
    is accepted and simply validates nothing, so the endpoint stays a pure predicate.
    """

    model_config = ConfigDict(extra="forbid")

    course_id: uuid.UUID
    section_ids: list[uuid.UUID] = Field(max_length=MAX_TARGET_SECTIONS)
    student_ids: list[uuid.UUID] = Field(max_length=MAX_TARGET_STUDENTS)


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

    @router.post("/api/v1/internal/course-targets/validate")
    def validate_course_targets(
        payload: CourseTargetValidationRequest,
        _caller: None = Depends(require_service_token),
        connection: Connection = Depends(dependencies.connection),
    ) -> dict[str, object]:
        """Answer "are these sections of this course, and are these students enrolled in them?".

        `CHG-0032`: B3 stores target ids it does not own the meaning of, so assignment creation
        must ask B4. The response deliberately returns ids and section labels only — no student
        name/number — because B3 needs an admission decision, not a second copy of identity data.
        """
        requested_sections = list(dict.fromkeys(payload.section_ids))
        requested_students = list(dict.fromkeys(payload.student_ids))

        section_rows = (
            connection.execute(
                select(
                    sections.c.section_id,
                    sections.c.course_id,
                    sections.c.section_code,
                    sections.c.term,
                )
                .where(sections.c.course_id == payload.course_id)
                .where(sections.c.section_id.in_(requested_sections))
                .order_by(sections.c.section_code, sections.c.section_id)
            ).fetchall()
            if requested_sections
            else []
        )
        valid_section_ids = {row[0] for row in section_rows}

        student_pairs: list[tuple[uuid.UUID, uuid.UUID]] = []
        if requested_students and valid_section_ids:
            rows = connection.execute(
                select(enrollments.c.user_id, enrollments.c.section_id)
                .select_from(
                    enrollments.join(sections, enrollments.c.section_id == sections.c.section_id)
                )
                .where(sections.c.course_id == payload.course_id)
                .where(enrollments.c.section_id.in_(tuple(valid_section_ids)))
                .where(enrollments.c.is_active.is_(True))
                .where(enrollments.c.user_id.is_not(None))
                .where(enrollments.c.user_id.in_(requested_students))
                .order_by(enrollments.c.section_id, enrollments.c.user_id)
            ).fetchall()
            student_pairs = [(row[0], row[1]) for row in rows]
        matched_students = {pair[0] for pair in student_pairs}

        logger.info(
            "internal.course_targets_validated",
            extra={
                "event": "internal.course_targets_validated",
                "course_id": str(payload.course_id),
                "requested_sections": len(requested_sections),
                "valid_sections": len(valid_section_ids),
                "requested_students": len(requested_students),
                "valid_students": len(matched_students),
            },
        )
        return {
            "course_id": str(payload.course_id),
            "sections": [
                {
                    "section_id": str(row[0]),
                    "course_id": str(row[1]),
                    "section_code": row[2],
                    "term": row[3],
                }
                for row in section_rows
            ],
            "unknown_section_ids": [
                str(item) for item in requested_sections if item not in valid_section_ids
            ],
            "students": [
                {"student_id": str(student_id), "section_id": str(section_id)}
                for student_id, section_id in student_pairs
            ],
            "unknown_student_ids": [
                str(item) for item in requested_students if item not in matched_students
            ],
        }

    return router
