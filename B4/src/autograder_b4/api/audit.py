"""Audit query API.

Visibility follows the same server-side scope rule as everything else: an administrator
sees every record, a teacher or assistant sees the records of the courses they are
scoped to, and a student sees nothing. Narrowing by course is applied as a filter, not as
a post-hoc redaction, so an out-of-scope record is never read from the database at all.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Connection, and_, select

from ..db.tables import audit_records
from ..errors import bad_request
from .dependencies import B4State, Dependencies
from .pagination import clamp_limit, descending_page_filter, page_envelope
from .problem import ProblemRoute

MAX_DETAIL_LENGTH = 500


def _rfc3339(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def build_audit_router(state: B4State, dependencies: Dependencies) -> APIRouter:
    router = APIRouter(route_class=ProblemRoute)

    @router.get("/api/v1/audit-records")
    def list_audit_records(
        caller=Depends(dependencies.principal),
        connection: Connection = Depends(dependencies.connection),
        cursor: str | None = Query(default=None),
        limit: int | None = Query(default=None, ge=1, le=200),
        course_id: uuid.UUID | None = Query(default=None),
        from_: datetime | None = Query(default=None, alias="from"),
        to: datetime | None = Query(default=None),
    ) -> dict[str, object]:
        size = clamp_limit(limit)
        if not caller.is_admin and not caller.scoped_course_ids:
            # A student (or an unscoped account) has no audit visibility at all; asking
            # is a permission error, not an empty page.
            from ..errors import forbidden

            raise forbidden("Audit records are visible to administrators and course staff only.")

        statement = select(audit_records).order_by(
            audit_records.c.occurred_at.desc(), audit_records.c.audit_id.desc()
        )
        if not caller.is_admin:
            allowed = sorted(caller.scoped_course_ids)
            if course_id is not None and course_id not in caller.scoped_course_ids:
                from ..errors import forbidden

                raise forbidden("Audit records for that course are not visible to the caller.")
            statement = statement.where(audit_records.c.course_id.in_(allowed))
        elif course_id is not None:
            statement = statement.where(audit_records.c.course_id == course_id)
        if from_ is not None:
            statement = statement.where(audit_records.c.occurred_at >= from_)
        if to is not None:
            statement = statement.where(audit_records.c.occurred_at <= to)
        if from_ is not None and to is not None and from_ > to:
            raise bad_request("The 'from' bound must not be later than the 'to' bound.")

        cursor_filter = descending_page_filter(
            audit_records.c.occurred_at, audit_records.c.audit_id, cursor
        )
        if cursor_filter is not None:
            statement = statement.where(
                and_(audit_records.c.occurred_at.is_not(None), cursor_filter)
            )
        rows = connection.execute(statement.limit(size)).fetchall()
        items = [
            {
                "audit_id": str(row.audit_id),
                "occurred_at": _rfc3339(row.occurred_at),
                "actor_user_id": str(row.actor_user_id) if row.actor_user_id else None,
                "action": row.action,
                "outcome": row.outcome,
                "target_type": row.target_type,
                "target_id": str(row.target_id) if row.target_id else None,
                "course_id": str(row.course_id) if row.course_id else None,
                "section_id": str(row.section_id) if row.section_id else None,
                "request_id": row.request_id,
                "correlation_id": row.correlation_id,
                "change_digest": row.change_digest,
                # `detail` is written by B4 and holds categories and field names, never
                # credentials or out-of-scope personal data.
                "detail": (row.detail or "")[:MAX_DETAIL_LENGTH],
            }
            for row in rows
        ]
        return page_envelope(items, [(row.occurred_at, row.audit_id) for row in rows], size)

    return router
