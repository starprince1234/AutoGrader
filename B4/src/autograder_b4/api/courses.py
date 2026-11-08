"""Course, section and enrollment APIs.

Authorization is by course scope, resolved server-side. A teacher is not trusted with
"courses" in general: `require_course_write` consults the course grants in `b4.roles`, so
a teacher acting on another teacher's course receives `403` -- hiding the button in B1
would have changed nothing.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection, and_, delete, func, select, update
from sqlalchemy.exc import IntegrityError

from ..audit import (
    ACTION_CLASS_CREATED,
    ACTION_CLASS_DELETED,
    ACTION_CLASS_RENAMED,
    ACTION_COURSE_CREATED,
    ACTION_COURSE_DELETED,
    ACTION_COURSE_RENAMED,
    ACTION_ENROLLMENT_DELETED,
    ACTION_ENROLLMENT_UPDATED,
    ACTION_NOTIFICATION_RETRIED,
    ACTION_SECTION_CREATED,
    ACTION_SECTION_DELETED,
    OUTCOME_SUCCESS,
    AuditLog,
    change_digest,
    changed_fields,
)
from ..authorization import (
    ROLE_TEACHER,
    course_id_of_section,
    require_any_platform_role,
    resolve_principal,
    visible_course_ids,
)
from ..classes import (
    class_row,
    course_roster_summary,
    create_class,
    delete_class,
    find_class_for_batch,
    list_class_summaries,
    rename_class,
    section_row,
    translate_integrity_error,
)
from ..db.tables import (
    classes,
    courses,
    enrollments,
    roster_imports,
    sections,
    users,
)
from ..db.tables import roles as roles_table
from ..errors import bad_request, conflict, forbidden, not_found
from ..logging import get_logger
from ..notifications import (
    campaign_for_teacher,
    deliveries,
    notification_id_of,
    retry_failed,
)
from .dependencies import (
    B4State,
    Dependencies,
    actor_of,
    audit_denial,
    ensure_course_read,
    ensure_course_write,
)
from .pagination import clamp_limit, descending_page_filter, page_envelope
from .problem import ProblemRoute
from .timestamps import rfc3339 as _rfc3339
from .timestamps import same_instant as _same_instant

logger = get_logger("courses")

MAX_CODE_LENGTH = 64
MAX_TITLE_LENGTH = 200
MAX_TERM_LENGTH = 64
MAX_STUDENT_NUMBER_LENGTH = 64
MAX_DISPLAY_NAME_LENGTH = 200
MAX_CLASS_NAME_LENGTH = 64


class CourseCreate(BaseModel):
    """A course needs a title; the code is an optional label (see `update_course`)."""

    model_config = ConfigDict(extra="forbid")

    course_code: str | None = Field(default=None, max_length=MAX_CODE_LENGTH)
    title: str = Field(min_length=1, max_length=MAX_TITLE_LENGTH)


class CourseRename(BaseModel):
    """Rename request: `title` only (see `update_course`), plus the optimistic lock."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=MAX_TITLE_LENGTH)
    #: Omitted = keep the stored code; `None`/empty = clear it.
    course_code: str | None = Field(default=None, max_length=MAX_CODE_LENGTH)
    expected_updated_at: datetime


class SectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_code: str = Field(min_length=1, max_length=MAX_CODE_LENGTH)
    term: str | None = Field(default=None, max_length=MAX_TERM_LENGTH)


class ClassCreateRequest(BaseModel):
    """Create one class inside an existing section (v0.32.0).

    With `batch_id` the class starts as a copy of that registered class's roster; without it
    the class starts empty and a roster can be uploaded into it afterwards.
    """

    model_config = ConfigDict(extra="forbid")

    class_name: str = Field(min_length=1, max_length=MAX_CLASS_NAME_LENGTH)
    batch_id: uuid.UUID | None = None


class ClassRenameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    class_name: str = Field(min_length=1, max_length=MAX_CLASS_NAME_LENGTH)


class EnrollmentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    student_number: str | None = Field(default=None, max_length=MAX_STUDENT_NUMBER_LENGTH)
    display_name: str | None = Field(default=None, min_length=1, max_length=MAX_DISPLAY_NAME_LENGTH)
    expected_updated_at: datetime | None = None


def _now() -> datetime:
    """Current time; the columns round it to the millisecond on store."""
    return datetime.now(UTC)


def _class_document(item: dict[str, object]) -> dict[str, object]:
    return {
        "class_id": item["class_id"],
        "course_id": item["course_id"],
        "section_id": item["section_id"],
        "class_name": item["class_name"],
        "source": item["source"],
        "allowlist_batch_id": item["allowlist_batch_id"],
        "enrollment_count": item["enrollment_count"],
        "bound_count": item["bound_count"],
        "created_at": _rfc3339(item["created_at"]),
    }


def _enrollment_document(row) -> dict[str, object]:
    """One roster row: identity fields plus where in the course it sits (v0.32.0)."""
    return {
        "enrollment_id": str(row.enrollment_id),
        "course_id": str(row.course_id),
        "section_id": str(row.section_id),
        "class_id": str(row.class_id),
        "class_name": row.class_name,
        "student_id": str(row.user_id) if row.user_id is not None else None,
        "student_number": row.student_number,
        "display_name": row.display_name,
        "is_active": bool(row.is_active),
        "updated_at": _rfc3339(row.updated_at),
    }


def _delivery_document(row) -> dict[str, object]:
    """One intended recipient of a publication notification, with its real outcome."""
    return {
        "delivery_id": str(row.delivery_id),
        "student_number": row.student_number,
        "display_name": row.display_name,
        "section_id": str(row.section_id),
        "class_id": str(row.class_id),
        "user_id": str(row.user_id) if row.user_id is not None else None,
        "status": row.status,
        "reason_code": row.reason_code,
        "reason_detail": row.reason_detail,
        "attempts": int(row.attempts),
        "sent_at": _rfc3339(row.sent_at) if row.sent_at is not None else None,
        "updated_at": _rfc3339(row.updated_at),
    }


def build_courses_router(state: B4State, dependencies: Dependencies) -> APIRouter:
    router = APIRouter(route_class=ProblemRoute)
    engine = state.engine

    @router.get("/api/v1/courses")
    def list_courses(
        caller=Depends(dependencies.principal),
        connection: Connection = Depends(dependencies.connection),
        cursor: str | None = Query(default=None),
        limit: int | None = Query(default=None, ge=1, le=200),
    ) -> dict[str, object]:
        size = clamp_limit(limit)
        visible = visible_course_ids(connection, caller)
        # The per-course section count is aggregated in the same query: one subquery
        # instead of one listSections call per course (N+1) for the course table.
        section_count = (
            select(sections.c.course_id, func.count().label("section_count"))
            .group_by(sections.c.course_id)
            .subquery()
        )
        statement = (
            select(courses, func.coalesce(section_count.c.section_count, 0).label("section_count"))
            .select_from(
                courses.outerjoin(section_count, section_count.c.course_id == courses.c.course_id)
            )
            .order_by(courses.c.created_at.desc(), courses.c.course_id.desc())
        )
        statement = statement.where(courses.c.is_active.is_(True))
        if visible is not None:
            if not visible:
                return {"items": [], "next_cursor": None}
            statement = statement.where(courses.c.course_id.in_(visible))
        cursor_filter = descending_page_filter(courses.c.created_at, courses.c.course_id, cursor)
        if cursor_filter is not None:
            statement = statement.where(cursor_filter)
        rows = connection.execute(statement.limit(size)).fetchall()
        items = [
            {
                "course_id": str(row.course_id),
                "course_code": row.course_code,
                "title": row.title,
                "updated_at": _rfc3339(row.updated_at),
                "section_count": row.section_count,
            }
            for row in rows
        ]
        return page_envelope(items, [(row.created_at, row.course_id) for row in rows], size)

    @router.post("/api/v1/courses", status_code=201)
    def create_course(
        payload: CourseCreate,
        caller=Depends(dependencies.principal),
    ) -> dict[str, object]:
        try:
            require_any_platform_role(caller, ROLE_TEACHER, "admin")
        except Exception as error:
            audit_denial(
                engine,
                principal=caller,
                detail="creating a course requires the teacher or admin role",
            )
            raise error
        course_id = uuid.uuid4()
        now = _now()
        raw_code = (payload.course_code or "").strip()
        code = raw_code or None
        title = payload.title.strip()
        with engine.begin() as connection:
            if code is not None:
                existing = connection.execute(
                    select(courses.c.course_id).where(courses.c.course_code == code)
                ).fetchone()
                if existing is not None:
                    raise conflict("A course with that code already exists.")
            connection.execute(
                courses.insert().values(
                    course_id=course_id,
                    course_code=code,
                    title=title,
                    created_by=caller.user_id,
                    is_active=True,
                    created_at=now,
                    updated_at=now,
                )
            )
            if not caller.is_admin:
                # Creating a course establishes the creator's course scope: without this
                # row the teacher would immediately be unable to manage what they made.
                connection.execute(
                    roles_table.insert().values(
                        role_id=uuid.uuid4(),
                        user_id=caller.user_id,
                        role=ROLE_TEACHER,
                        course_id=course_id,
                        granted_by=caller.user_id,
                        created_at=now,
                    )
                )
            AuditLog(connection).record(
                action=ACTION_COURSE_CREATED,
                outcome=OUTCOME_SUCCESS,
                actor=actor_of(caller),
                target_type="course",
                target_id=course_id,
                course_id=course_id,
                detail=f"created course {code}",
            )
        logger.info(
            "course.created", extra={"event": "course.created", "course_id": str(course_id)}
        )
        return {
            "course_id": str(course_id),
            "course_code": code,
            "title": title,
            "updated_at": _rfc3339(now),
            "section_count": 0,
        }

    @router.patch("/api/v1/courses/{course_id}")
    def update_course(
        course_id: uuid.UUID,
        payload: CourseRename,
        caller=Depends(dependencies.principal),
        connection: Connection = Depends(dependencies.connection),
    ) -> dict[str, object]:
        """Rename one course; the code is an identity and is not writable here."""
        row = connection.execute(
            select(courses).where(courses.c.course_id == course_id)
        ).fetchone()
        if row is None:
            raise not_found("The course does not exist.")
        try:
            ensure_course_write(principal=caller, course_id=course_id, dependencies=dependencies)
        except Exception as error:
            audit_denial(
                engine,
                principal=caller,
                detail="renaming a course requires write access to that course",
            )
            raise error

        title = payload.title.strip()
        if not title:
            raise bad_request("The course title must not be blank.")
        if not _same_instant(row.updated_at, payload.expected_updated_at):
            raise conflict("The course changed since it was read; reload before saving.")

        now = _now()
        values: dict[str, object] = {"title": title, "updated_at": now}
        if "course_code" in payload.model_fields_set:
            raw_code = (payload.course_code or "").strip()
            values["course_code"] = raw_code or None
            if raw_code:
                clash = connection.execute(
                    select(courses.c.course_id).where(
                        courses.c.course_code == raw_code,
                        courses.c.course_id != course_id,
                    )
                ).fetchone()
                if clash is not None:
                    raise conflict("A course with that code already exists.")

        with engine.begin() as write_connection:
            write_connection.execute(
                update(courses).where(courses.c.course_id == course_id).values(**values)
            )
            AuditLog(write_connection).record(
                action=ACTION_COURSE_RENAMED,
                outcome=OUTCOME_SUCCESS,
                actor=actor_of(caller),
                target_type="course",
                target_id=course_id,
                course_id=course_id,
                detail=f"renamed course title: {row.title!r} -> {title!r}",
                digest=change_digest({"title": title}),
            )
        section_total = connection.execute(
            select(func.count()).select_from(sections).where(sections.c.course_id == course_id)
        ).scalar_one()
        return {
            "course_id": str(course_id),
            "course_code": values.get("course_code", row.course_code),
            "title": title,
            "updated_at": _rfc3339(now),
            "section_count": int(section_total),
        }

    @router.get("/api/v1/courses/{course_id}/sections")
    def list_sections(
        course_id: uuid.UUID,
        caller=Depends(dependencies.principal),
        connection: Connection = Depends(dependencies.connection),
    ) -> dict[str, object]:
        exists = connection.execute(
            select(courses.c.course_id).where(courses.c.course_id == course_id)
        ).fetchone()
        if exists is None:
            raise not_found("The course does not exist.")
        ensure_course_read(principal=caller, course_id=course_id, dependencies=dependencies)
        # A section is a teaching group, not a class: several classes may sit under one
        # section (v0.32.0), so the response reports both counts and no longer borrows a
        # class name from the latest roster import.
        class_count = (
            select(classes.c.section_id, func.count().label("class_count"))
            .group_by(classes.c.section_id)
            .subquery()
        )
        enrollment_count = (
            select(enrollments.c.section_id, func.count().label("enrollment_count"))
            .where(enrollments.c.is_active.is_(True))
            .group_by(enrollments.c.section_id)
            .subquery()
        )
        rows = connection.execute(
            select(
                sections,
                func.coalesce(class_count.c.class_count, 0).label("class_count"),
                func.coalesce(enrollment_count.c.enrollment_count, 0).label("enrollment_count"),
            )
            .select_from(
                sections.outerjoin(
                    class_count, class_count.c.section_id == sections.c.section_id
                ).outerjoin(
                    enrollment_count,
                    enrollment_count.c.section_id == sections.c.section_id,
                )
            )
            .where(sections.c.course_id == course_id)
            .order_by(sections.c.section_code)
        ).fetchall()
        return {
            "items": [
                {
                    "section_id": str(row.section_id),
                    "course_id": str(row.course_id),
                    "section_code": row.section_code,
                    "term": row.term,
                    "class_count": row.class_count,
                    "enrollment_count": row.enrollment_count,
                    "created_at": _rfc3339(row.created_at),
                }
                for row in rows
            ]
        }

    @router.get("/api/v1/sections/{section_id}/classes")
    def list_classes(
        section_id: uuid.UUID,
        caller=Depends(dependencies.principal),
        connection: Connection = Depends(dependencies.connection),
    ) -> dict[str, object]:
        section = section_row(connection, section_id)
        if section is None:
            raise not_found("The section does not exist.")
        ensure_course_read(
            principal=caller, course_id=section.course_id, dependencies=dependencies
        )
        return {
            "items": [
                _class_document(item) for item in list_class_summaries(connection, section_id)
            ]
        }

    @router.post("/api/v1/sections/{section_id}/classes", status_code=201)
    def create_class_endpoint(
        section_id: uuid.UUID,
        payload: ClassCreateRequest,
        caller=Depends(dependencies.principal),
    ) -> dict[str, object]:
        """Create one class inside an existing section; never creates a section.

        Attaching a registered class (`batch_id`) copies that class's students into the new
        class. Re-attaching the same batch to the same section is idempotent; any student
        already active in another section/class of this course makes the whole request `409`
        and nothing is written.
        """
        now = _now()
        try:
            with engine.begin() as write:
                section = section_row(write, section_id)
                if section is None:
                    raise not_found("The section does not exist.")
                course_id = section.course_id
                try:
                    ensure_course_write(
                        principal=caller, course_id=course_id, dependencies=dependencies
                    )
                except Exception as error:
                    audit_denial(
                        engine,
                        principal=caller,
                        detail="creating a class requires write access to its course",
                    )
                    raise error
                summary, existed = create_class(
                    write,
                    section_id=section_id,
                    class_name=payload.class_name,
                    batch_id=payload.batch_id,
                    actor_user_id=caller.user_id,
                    now=now,
                )
                if not existed:
                    AuditLog(write).record(
                        action=ACTION_CLASS_CREATED,
                        outcome=OUTCOME_SUCCESS,
                        actor=actor_of(caller),
                        target_type="class",
                        target_id=uuid.UUID(str(summary["class_id"])),
                        course_id=course_id,
                        section_id=section_id,
                        detail=(
                            f"created class {summary['class_name']!r} "
                            f"({summary['enrollment_count']} student(s))"
                        ),
                    )
        except IntegrityError as error:
            # A concurrent identical attach won the unique-index race. Return the class it
            # created instead of a 500; anything else is a real conflict and is translated.
            if payload.batch_id is not None:
                with engine.begin() as retry:
                    section = section_row(retry, section_id)
                    if section is not None:
                        ensure_course_write(
                            principal=caller,
                            course_id=section.course_id,
                            dependencies=dependencies,
                        )
                        existing = find_class_for_batch(
                            retry, section_id=section_id, batch_id=payload.batch_id
                        )
                        if existing is not None:
                            return _class_document(existing)
            raise translate_integrity_error(error) from error
        logger.info(
            "class.created",
            extra={
                "event": "class.created",
                "section_id": str(section_id),
                "class_id": summary["class_id"],
                "existed": existed,
            },
        )
        return _class_document(summary)

    @router.patch("/api/v1/classes/{class_id}")
    def rename_class_endpoint(
        class_id: uuid.UUID,
        payload: ClassRenameRequest,
        caller=Depends(dependencies.principal),
    ) -> dict[str, object]:
        now = _now()
        with engine.begin() as write:
            row = class_row(write, class_id)
            if row is None:
                raise not_found("The class does not exist.")
            section = section_row(write, row.section_id)
            if section is None:  # pragma: no cover - guaranteed by the foreign key
                raise not_found("The section does not exist.")
            course_id = section.course_id
            try:
                ensure_course_write(
                    principal=caller, course_id=course_id, dependencies=dependencies
                )
            except Exception as error:
                audit_denial(
                    engine,
                    principal=caller,
                    detail="renaming a class requires write access to its course",
                )
                raise error
            try:
                summary = rename_class(
                    write, class_id=class_id, class_name=payload.class_name, now=now
                )
            except IntegrityError as error:
                raise translate_integrity_error(error) from error
            AuditLog(write).record(
                action=ACTION_CLASS_RENAMED,
                outcome=OUTCOME_SUCCESS,
                actor=actor_of(caller),
                target_type="class",
                target_id=class_id,
                course_id=course_id,
                section_id=row.section_id,
                detail=f"renamed class {row.class_name!r} -> {summary['class_name']!r}",
            )
        return _class_document(summary)

    @router.delete("/api/v1/classes/{class_id}", status_code=204)
    def delete_class_endpoint(
        class_id: uuid.UUID,
        caller=Depends(dependencies.principal),
        connection: Connection = Depends(dependencies.connection),
    ) -> Response:
        row = class_row(connection, class_id)
        if row is None:
            raise not_found("The class does not exist.")
        section = section_row(connection, row.section_id)
        if section is None:  # pragma: no cover - guaranteed by the foreign key
            raise not_found("The section does not exist.")
        course_id = section.course_id
        try:
            ensure_course_write(
                principal=caller, course_id=course_id, dependencies=dependencies
            )
        except Exception as error:
            audit_denial(
                engine,
                principal=caller,
                detail="deleting a class requires write access to its course",
            )
            raise error
        try:
            with engine.begin() as write:
                deleted = delete_class(write, class_id=class_id)
                AuditLog(write).record(
                    action=ACTION_CLASS_DELETED,
                    outcome=OUTCOME_SUCCESS,
                    actor=actor_of(caller),
                    target_type="class",
                    target_id=class_id,
                    course_id=course_id,
                    section_id=row.section_id,
                    detail=f"deleted class {deleted.class_name!r} with its roster rows",
                )
        except IntegrityError as error:
            raise conflict("The class is still referenced and cannot be deleted.") from error
        return Response(status_code=204)

    @router.get("/api/v1/classes/{class_id}/enrollments")
    def list_class_enrollments(
        class_id: uuid.UUID,
        caller=Depends(dependencies.principal),
        connection: Connection = Depends(dependencies.connection),
        cursor: str | None = Query(default=None),
        limit: int | None = Query(default=None, ge=1, le=200),
    ) -> dict[str, object]:
        size = clamp_limit(limit)
        row = class_row(connection, class_id)
        if row is None:
            raise not_found("The class does not exist.")
        section = section_row(connection, row.section_id)
        if section is None:  # pragma: no cover - guaranteed by the foreign key
            raise not_found("The section does not exist.")
        ensure_course_read(
            principal=caller, course_id=section.course_id, dependencies=dependencies
        )
        statement = (
            select(enrollments, classes.c.class_name)
            .select_from(
                enrollments.join(classes, enrollments.c.class_id == classes.c.class_id)
            )
            .where(enrollments.c.class_id == class_id)
            .order_by(enrollments.c.created_at.desc(), enrollments.c.enrollment_id.desc())
        )
        cursor_filter = descending_page_filter(
            enrollments.c.created_at, enrollments.c.enrollment_id, cursor
        )
        if cursor_filter is not None:
            statement = statement.where(cursor_filter)
        rows = connection.execute(statement.limit(size)).fetchall()
        items = [_enrollment_document(row) for row in rows]
        return page_envelope(
            items, [(row.created_at, row.enrollment_id) for row in rows], size
        )

    @router.get("/api/v1/courses/{course_id}/enrollments")
    def list_course_enrollments(
        course_id: uuid.UUID,
        caller=Depends(dependencies.principal),
        connection: Connection = Depends(dependencies.connection),
        cursor: str | None = Query(default=None),
        limit: int | None = Query(default=None, ge=1, le=200),
        section_id: uuid.UUID | None = Query(default=None),
        class_id: uuid.UUID | None = Query(default=None),
    ) -> dict[str, object]:
        """Page through the complete course roster (all sections and classes).

        The teacher detail page reads this; the row set is the audience, including students
        without an account. Paginating here is honest only because the caller pages to the
        end (an incomplete page must never be presented as "this is everybody").
        """
        size = clamp_limit(limit)
        exists = connection.execute(
            select(courses.c.course_id).where(courses.c.course_id == course_id)
        ).fetchone()
        if exists is None:
            raise not_found("The course does not exist.")
        ensure_course_read(principal=caller, course_id=course_id, dependencies=dependencies)
        statement = (
            select(enrollments, classes.c.class_name)
            .select_from(
                enrollments.join(classes, enrollments.c.class_id == classes.c.class_id)
            )
            .where(enrollments.c.course_id == course_id)
            .order_by(enrollments.c.created_at.desc(), enrollments.c.enrollment_id.desc())
        )
        if section_id is not None:
            statement = statement.where(enrollments.c.section_id == section_id)
        if class_id is not None:
            statement = statement.where(enrollments.c.class_id == class_id)
        cursor_filter = descending_page_filter(
            enrollments.c.created_at, enrollments.c.enrollment_id, cursor
        )
        if cursor_filter is not None:
            statement = statement.where(cursor_filter)
        rows = connection.execute(statement.limit(size)).fetchall()
        items = [_enrollment_document(row) for row in rows]
        return page_envelope(items, [(row.created_at, row.enrollment_id) for row in rows], size)

    @router.get("/api/v1/courses/{course_id}/roster-summary")
    def get_course_roster_summary(
        course_id: uuid.UUID,
        caller=Depends(dependencies.principal),
        connection: Connection = Depends(dependencies.connection),
    ) -> dict[str, object]:
        ensure_course_read(principal=caller, course_id=course_id, dependencies=dependencies)
        return course_roster_summary(connection, course_id)

    @router.get("/api/v1/courses/{course_id}/assignments/{assignment_id}/notifications")
    def get_assignment_notifications(
        course_id: uuid.UUID,
        assignment_id: uuid.UUID,
        caller=Depends(dependencies.principal),
        connection: Connection = Depends(dependencies.connection),
        cursor: str | None = Query(default=None),
        limit: int | None = Query(default=None, ge=1, le=200),
    ) -> dict[str, object]:
        """Staff-only view of one assignment's publication notification (summary + rows)."""
        size = clamp_limit(limit)
        ensure_course_write(
            principal=caller, course_id=course_id, dependencies=dependencies
        )
        summary = campaign_for_teacher(
            connection, course_id=course_id, assignment_id=assignment_id
        )
        notification_id = notification_id_of(
            connection, course_id=course_id, assignment_id=assignment_id
        )
        statement = (
            select(deliveries)
            .where(deliveries.c.notification_id == notification_id)
            .order_by(deliveries.c.created_at.desc(), deliveries.c.delivery_id.desc())
        )
        cursor_filter = descending_page_filter(
            deliveries.c.created_at, deliveries.c.delivery_id, cursor
        )
        if cursor_filter is not None:
            statement = statement.where(cursor_filter)
        rows = connection.execute(statement.limit(size)).fetchall()
        items = [_delivery_document(row) for row in rows]
        return {
            "summary": summary,
            "deliveries": page_envelope(
                items, [(row.created_at, row.delivery_id) for row in rows], size
            ),
        }

    @router.post(
        "/api/v1/courses/{course_id}/assignments/{assignment_id}/notifications/retry"
    )
    def retry_assignment_notifications(
        course_id: uuid.UUID,
        assignment_id: uuid.UUID,
        caller=Depends(dependencies.principal),
    ) -> dict[str, object]:
        """Re-arm transport failures; skipped identity reasons are shown, not retried."""
        ensure_course_write(
            principal=caller, course_id=course_id, dependencies=dependencies
        )
        summary = retry_failed(
            engine, course_id=course_id, assignment_id=assignment_id, now=_now()
        )
        with engine.begin() as write:
            AuditLog(write).record(
                action=ACTION_NOTIFICATION_RETRIED,
                outcome=OUTCOME_SUCCESS,
                actor=actor_of(caller),
                target_type="assignment_notification",
                target_id=assignment_id,
                course_id=course_id,
                detail=(
                    f"re-armed failed deliveries: pending={summary['pending_count']} "
                    f"failed={summary['failed_count']}"
                ),
            )
        return summary

    @router.post("/api/v1/courses/{course_id}/sections", status_code=201)
    def create_section(
        course_id: uuid.UUID,
        payload: SectionCreate,
        caller=Depends(dependencies.principal),
    ) -> dict[str, object]:
        section_id = uuid.uuid4()
        now = _now()
        with engine.begin() as connection:
            course = connection.execute(
                select(courses.c.course_id).where(courses.c.course_id == course_id)
            ).fetchone()
            if course is None:
                raise not_found("The course does not exist.")
            caller = resolve_principal(connection, caller.user_id)
            ensure_course_write(principal=caller, course_id=course_id, dependencies=dependencies)
            code = payload.section_code.strip()
            term = (payload.term or "").strip() or None
            existing = connection.execute(
                select(sections.c.section_id).where(
                    and_(
                        sections.c.course_id == course_id,
                        sections.c.section_code == code,
                        sections.c.term.is_(None) if term is None else sections.c.term == term,
                    )
                )
            ).fetchone()
            if existing is not None:
                raise conflict("A section with that code and term already exists.")
            connection.execute(
                sections.insert().values(
                    section_id=section_id,
                    course_id=course_id,
                    section_code=code,
                    term=term,
                    created_by=caller.user_id,
                    created_at=now,
                    updated_at=now,
                )
            )
            AuditLog(connection).record(
                action=ACTION_SECTION_CREATED,
                outcome=OUTCOME_SUCCESS,
                actor=actor_of(caller),
                target_type="section",
                target_id=section_id,
                course_id=course_id,
                section_id=section_id,
                detail=f"created section {code}",
            )
        logger.info(
            "section.created",
            extra={
                "event": "section.created",
                "course_id": str(course_id),
                "section_id": str(section_id),
            },
        )
        return {
            "section_id": str(section_id),
            "course_id": str(course_id),
            "section_code": code,
            "term": term,
            "class_count": 0,
            "created_at": _rfc3339(now),
            "enrollment_count": 0,
        }

    @router.get("/api/v1/sections/{section_id}/enrollments")
    def list_enrollments(
        section_id: uuid.UUID,
        caller=Depends(dependencies.principal),
        connection: Connection = Depends(dependencies.connection),
        cursor: str | None = Query(default=None),
        limit: int | None = Query(default=None, ge=1, le=200),
    ) -> dict[str, object]:
        size = clamp_limit(limit)
        course_id = course_id_of_section(connection, section_id)
        if course_id is None:
            raise not_found("The section does not exist.")
        ensure_course_read(principal=caller, course_id=course_id, dependencies=dependencies)
        statement = (
            select(enrollments, classes.c.class_name)
            .select_from(
                enrollments.join(classes, enrollments.c.class_id == classes.c.class_id)
            )
            .where(enrollments.c.section_id == section_id)
            .order_by(enrollments.c.created_at.desc(), enrollments.c.enrollment_id.desc())
        )
        cursor_filter = descending_page_filter(
            enrollments.c.created_at, enrollments.c.enrollment_id, cursor
        )
        if cursor_filter is not None:
            statement = statement.where(cursor_filter)
        rows = connection.execute(statement.limit(size)).fetchall()
        items = [_enrollment_document(row) for row in rows]
        return page_envelope(items, [(row.created_at, row.enrollment_id) for row in rows], size)

    @router.patch("/api/v1/enrollments/{enrollment_id}")
    def update_enrollment(
        enrollment_id: uuid.UUID,
        payload: EnrollmentUpdate,
        caller=Depends(dependencies.principal),
    ) -> dict[str, object]:
        now = _now()
        with engine.begin() as connection:
            row = connection.execute(
                select(enrollments).where(enrollments.c.enrollment_id == enrollment_id)
            ).fetchone()
            if row is None:
                raise not_found("The enrollment does not exist.")
            course_id = course_id_of_section(connection, row.section_id)
            if course_id is None:  # pragma: no cover - guaranteed by the foreign key
                raise not_found("The section does not exist.")
            caller = resolve_principal(connection, caller.user_id)
            ensure_course_write(principal=caller, course_id=course_id, dependencies=dependencies)
            if payload.expected_updated_at is not None and not _same_instant(
                row.updated_at, payload.expected_updated_at
            ):
                raise conflict(
                    "The roster entry was modified since it was read; re-read and retry."
                )
            before = {"student_number": row.student_number, "display_name": row.display_name}
            updates: dict[str, object] = {}
            if payload.student_number is not None:
                number = payload.student_number.strip()
                if not number:
                    raise bad_request("student_number must not be blank.")
                updates["student_number"] = number
            if payload.display_name is not None:
                updates["display_name"] = payload.display_name.strip()
            if updates:
                # v0.32.0: the collision check is course-wide, because the uniqueness rule
                # is "one active section/class per student per course" -- not per section.
                proposed_number = updates.get("student_number", row.student_number)
                clash = connection.execute(
                    select(enrollments.c.enrollment_id).where(
                        and_(
                            enrollments.c.course_id == row.course_id,
                            enrollments.c.student_number == proposed_number,
                            enrollments.c.is_active.is_(True),
                            enrollments.c.enrollment_id != enrollment_id,
                        )
                    )
                ).fetchone()
                if clash is not None:
                    raise conflict(
                        "This student is already actively enrolled in another section/class "
                        "of this course; one student may have only one active section per "
                        "course. Remove the other roster row first."
                    )
                target_name = updates.get("display_name", row.display_name)
                if row.user_id is not None:
                    linked = connection.execute(
                        select(users.c.student_number, users.c.display_name).where(
                            users.c.user_id == row.user_id
                        )
                    ).fetchone()
                    if linked is not None and (
                        str(linked.student_number or "") != proposed_number
                        or str(linked.display_name).strip() != target_name
                    ):
                        raise conflict(
                            "This roster row is linked to an account whose student number "
                            "or name differs from the edited values; correct the account "
                            "first so the two records cannot describe two different people."
                        )
                updates["updated_at"] = now
                try:
                    connection.execute(
                        update(enrollments)
                        .where(enrollments.c.enrollment_id == enrollment_id)
                        .values(**updates)
                    )
                except IntegrityError as error:
                    raise translate_integrity_error(error) from error
            after = {
                "student_number": updates.get("student_number", row.student_number),
                "display_name": updates.get("display_name", row.display_name),
            }
            fields = changed_fields(before, after)
            AuditLog(connection).record(
                action=ACTION_ENROLLMENT_UPDATED,
                outcome=OUTCOME_SUCCESS,
                actor=actor_of(caller),
                target_type="enrollment",
                target_id=enrollment_id,
                course_id=course_id,
                section_id=row.section_id,
                digest=change_digest(before, after),
                detail=f"changed fields: {', '.join(fields) or 'none'}",
            )
        logger.info(
            "enrollment.updated",
            extra={"event": "enrollment.updated", "enrollment_id": str(enrollment_id)},
        )
        return {"enrollment_id": str(enrollment_id), "changed_fields": fields}

    @router.delete("/api/v1/enrollments/{enrollment_id}", status_code=204)
    def delete_enrollment(
        enrollment_id: uuid.UUID,
        caller=Depends(dependencies.principal),
    ) -> Response:
        with engine.begin() as connection:
            row = connection.execute(
                select(enrollments).where(enrollments.c.enrollment_id == enrollment_id)
            ).fetchone()
            if row is None:
                raise not_found("The enrollment does not exist.")
            course_id = course_id_of_section(connection, row.section_id)
            if course_id is None:  # pragma: no cover - guaranteed by the foreign key
                raise not_found("The section does not exist.")
            caller = resolve_principal(connection, caller.user_id)
            ensure_course_write(principal=caller, course_id=course_id, dependencies=dependencies)
            connection.execute(
                enrollments.delete().where(enrollments.c.enrollment_id == enrollment_id)
            )
            AuditLog(connection).record(
                action=ACTION_ENROLLMENT_DELETED,
                outcome=OUTCOME_SUCCESS,
                actor=actor_of(caller),
                target_type="enrollment",
                target_id=enrollment_id,
                course_id=course_id,
                section_id=row.section_id,
                detail="roster entry removed",
            )
        logger.info(
            "enrollment.deleted",
            extra={"event": "enrollment.deleted", "enrollment_id": str(enrollment_id)},
        )
        return Response(status_code=204)

    @router.delete("/api/v1/courses/{course_id}/sections/{section_id}", status_code=204)
    def delete_section(
        course_id: uuid.UUID,
        section_id: uuid.UUID,
        caller=Depends(dependencies.principal),
        connection: Connection = Depends(dependencies.connection),
    ) -> Response:
        """Hard delete: the section, its classes and their roster rows go; the course stays."""
        row = connection.execute(
            select(sections).where(
                sections.c.section_id == section_id, sections.c.course_id == course_id
            )
        ).fetchone()
        if row is None:
            raise not_found("The section does not exist in this course.")
        try:
            ensure_course_write(principal=caller, course_id=course_id, dependencies=dependencies)
        except Exception as error:
            audit_denial(
                engine,
                principal=caller,
                detail="deleting a section requires write access to its course",
            )
            raise error

        try:
            with engine.begin() as write:
                # `roster_imports.section_id` is ON DELETE RESTRICT, so the import rows must
                # go first; their files and parsed rows cascade from them. Classes then
                # cascade their enrollments, and the section goes last.
                write.execute(
                    delete(roster_imports).where(roster_imports.c.section_id == section_id)
                )
                write.execute(delete(classes).where(classes.c.section_id == section_id))
                write.execute(delete(sections).where(sections.c.section_id == section_id))
                AuditLog(write).record(
                    action=ACTION_SECTION_DELETED,
                    outcome=OUTCOME_SUCCESS,
                    actor=actor_of(caller),
                    target_type="section",
                    target_id=section_id,
                    course_id=course_id,
                    detail=f"deleted section {row.section_code!r} with its classes and roster rows",
                )
        except IntegrityError as error:
            # A reference from another schema can still block it: report 409, not 500.
            raise conflict("The class is still referenced and cannot be deleted.") from error
        return Response(status_code=204)

    @router.delete("/api/v1/courses/{course_id}", status_code=204)
    def delete_course(
        course_id: uuid.UUID,
        caller=Depends(dependencies.principal),
        connection: Connection = Depends(dependencies.connection),
    ) -> Response:
        """Delete a course together with its classes and roster rows."""
        row = connection.execute(
            select(courses).where(courses.c.course_id == course_id)
        ).fetchone()
        if row is None:
            raise not_found("The course does not exist.")
        try:
            ensure_course_write(principal=caller, course_id=course_id, dependencies=dependencies)
        except Exception as error:
            audit_denial(
                engine,
                principal=caller,
                detail="deleting a course requires write access to it",
            )
            raise error

        try:
            with engine.begin() as write:
                # FK order: roster imports/sections/classes are RESTRICT-or-cascade, roles
                # cascade from the course, audit rows null out their reference. Classes are
                # deleted before sections; their enrollments cascade with them.
                section_ids = [
                    value[0]
                    for value in write.execute(
                        select(sections.c.section_id).where(sections.c.course_id == course_id)
                    ).fetchall()
                ]
                write.execute(
                    delete(roster_imports).where(roster_imports.c.course_id == course_id)
                )
                if section_ids:
                    # `classes` has no course_id: the section is the course link (v0.32.0).
                    write.execute(
                        delete(classes).where(classes.c.section_id.in_(section_ids))
                    )
                write.execute(delete(sections).where(sections.c.course_id == course_id))
                write.execute(delete(courses).where(courses.c.course_id == course_id))
                AuditLog(write).record(
                    action=ACTION_COURSE_DELETED,
                    outcome=OUTCOME_SUCCESS,
                    actor=actor_of(caller),
                    target_type="course",
                    target_id=course_id,
                    course_id=None,
                    detail=(
                        f"deleted course {row.course_code!r} / {row.title!r} "
                        f"with {len(section_ids)} section(s)"
                    ),
                )
        except IntegrityError as error:
            raise conflict(
                "The course is still referenced by records outside this module "
                "and cannot be deleted."
            ) from error
        return Response(status_code=204)

    return router


__all__ = ["build_courses_router", "forbidden"]
