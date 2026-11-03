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
    ACTION_COURSE_CREATED,
    ACTION_COURSE_DELETED,
    ACTION_COURSE_RENAMED,
    ACTION_ENROLLMENT_DELETED,
    ACTION_ENROLLMENT_UPDATED,
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
from ..db.tables import (
    courses,
    enrollments,
    registration_allowlist,
    registration_allowlist_batches,
    roster_imports,
    sections,
)
from ..db.tables import roles as roles_table
from ..errors import bad_request, conflict, forbidden, not_found
from ..logging import get_logger
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


class RegisteredClassImport(BaseModel):
    """Which registration-allowlist class to attach to the course."""

    model_config = ConfigDict(extra="forbid")

    batch_id: uuid.UUID
    section_code: str | None = Field(default=None, max_length=MAX_CODE_LENGTH)


class SectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_code: str = Field(min_length=1, max_length=MAX_CODE_LENGTH)
    term: str | None = Field(default=None, max_length=MAX_TERM_LENGTH)


class EnrollmentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    student_number: str | None = Field(default=None, max_length=MAX_STUDENT_NUMBER_LENGTH)
    display_name: str | None = Field(default=None, min_length=1, max_length=MAX_DISPLAY_NAME_LENGTH)
    expected_updated_at: datetime | None = None


def domain_class_name(filename: str) -> str:
    """Class name = file name without the extension, same rule as the allowlist."""
    stem = filename.strip().rsplit("/", 1)[-1]
    for suffix in (".csv", ".CSV"):
        if stem.endswith(suffix):
            return stem[: -len(suffix)].strip() or stem
    return stem


def _now() -> datetime:
    """Current time; the columns round it to the millisecond on store."""
    return datetime.now(UTC)


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
        enrollment_count = (
            select(enrollments.c.section_id, func.count().label("enrollment_count"))
            .group_by(enrollments.c.section_id)
            .subquery()
        )
        # Class name comes from the most recent successful roster import:
        # teachers recognise the file they imported, not the internal section code.
        latest_import = (
            select(
                roster_imports.c.section_id,
                roster_imports.c.filename,
                func.row_number()
                .over(
                    partition_by=roster_imports.c.section_id,
                    order_by=roster_imports.c.created_at.desc(),
                )
                .label("rank"),
            ).subquery()
        )
        rows = connection.execute(
            select(
                sections,
                func.coalesce(enrollment_count.c.enrollment_count, 0).label("enrollment_count"),
                latest_import.c.filename,
            )
            .select_from(
                sections.outerjoin(
                    enrollment_count, enrollment_count.c.section_id == sections.c.section_id
                ).outerjoin(
                    latest_import,
                    and_(
                        latest_import.c.section_id == sections.c.section_id,
                        latest_import.c.rank == 1,
                        latest_import.c.filename.is_not(None),
                    ),
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
                    "enrollment_count": row.enrollment_count,
                    "class_name": row.filename,
                    "created_at": _rfc3339(row.created_at),
                }
                for row in rows
            ]
        }

    @router.post("/api/v1/courses/{course_id}/classes", status_code=201)
    def attach_registered_class(
        course_id: uuid.UUID,
        payload: RegisteredClassImport,
        caller=Depends(dependencies.principal),
    ) -> dict[str, object]:
        """Create a class from an already-registered class (allowlist batch)."""
        with engine.begin() as write:
            course_row = write.execute(
                select(courses).where(courses.c.course_id == course_id)
            ).fetchone()
            if course_row is None:
                raise not_found("The course does not exist.")
            try:
                ensure_course_write(
                    principal=caller, course_id=course_id, dependencies=dependencies
                )
            except Exception as error:
                audit_denial(
                    engine,
                    principal=caller,
                    detail="attaching a class requires write access to its course",
                )
                raise error

            batch = write.execute(
                select(registration_allowlist_batches).where(
                    registration_allowlist_batches.c.batch_id == payload.batch_id
                )
            ).fetchone()
            if batch is None:
                raise not_found("The registered class does not exist.")

            entries = write.execute(
                select(
                    registration_allowlist.c.student_number,
                    registration_allowlist.c.display_name,
                )
                .where(registration_allowlist.c.batch_id == payload.batch_id)
                .order_by(registration_allowlist.c.student_number)
            ).fetchall()

            base_code = (
                payload.section_code.strip()
                if payload.section_code and payload.section_code.strip()
                else domain_class_name(batch.filename)
            )
            taken = {
                row[0]
                for row in write.execute(
                    select(sections.c.section_code).where(sections.c.course_id == course_id)
                ).fetchall()
            }
            code = base_code
            suffix = 2
            while code in taken:
                code = f"{base_code}-{suffix}"
                suffix += 1

            section_id = uuid.uuid4()
            now = _now()
            write.execute(
                sections.insert().values(
                    section_id=section_id,
                    course_id=course_id,
                    section_code=code,
                    term=None,
                    created_by=caller.user_id,
                    created_at=now,
                )
            )
            if entries:
                write.execute(
                    enrollments.insert(),
                    [
                        {
                            "enrollment_id": uuid.uuid4(),
                            "section_id": section_id,
                            "user_id": None,
                            "student_number": entry.student_number,
                            "display_name": entry.display_name,
                            "source": "registered_class",
                            "roster_import_id": None,
                            "is_active": True,
                            "created_at": now,
                            "updated_at": now,
                        }
                        for entry in entries
                    ],
                )
            AuditLog(write).record(
                action=ACTION_SECTION_CREATED,
                outcome=OUTCOME_SUCCESS,
                actor=actor_of(caller),
                target_type="section",
                target_id=section_id,
                course_id=course_id,
                detail=(
                    f"class {code!r} from registered class {batch.filename!r} "
                    f"({len(entries)} students)"
                ),
            )
        return {
            "section_id": str(section_id),
            "course_id": str(course_id),
            "section_code": code,
            "term": None,
            "enrollment_count": len(entries),
            "class_name": batch.filename,
        }

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
            select(enrollments)
            .where(enrollments.c.section_id == section_id)
            .order_by(enrollments.c.created_at.desc(), enrollments.c.enrollment_id.desc())
        )
        cursor_filter = descending_page_filter(
            enrollments.c.created_at, enrollments.c.enrollment_id, cursor
        )
        if cursor_filter is not None:
            statement = statement.where(cursor_filter)
        rows = connection.execute(statement.limit(size)).fetchall()
        items = [
            {
                "enrollment_id": str(row.enrollment_id),
                "section_id": str(row.section_id),
                # The platform user id, not the roster's student number: it is the only key
                # a roster row shares with another module's data (CHG-0007), and it is null
                # when the roster row was never matched to a user account.
                "student_id": str(row.user_id) if row.user_id is not None else None,
                "student_number": row.student_number,
                "display_name": row.display_name,
                "is_active": row.is_active,
                "updated_at": _rfc3339(row.updated_at),
            }
            for row in rows
        ]
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
                clash = connection.execute(
                    select(enrollments.c.enrollment_id).where(
                        and_(
                            enrollments.c.section_id == row.section_id,
                            enrollments.c.student_number
                            == updates.get("student_number", row.student_number),
                            enrollments.c.enrollment_id != enrollment_id,
                        )
                    )
                ).fetchone()
                if clash is not None:
                    raise conflict("Another roster entry already uses that student number.")
                updates["updated_at"] = now
                connection.execute(
                    update(enrollments)
                    .where(enrollments.c.enrollment_id == enrollment_id)
                    .values(**updates)
                )
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
        """Hard delete: the class and its roster rows go; the course stays."""
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
                detail="deleting a class requires write access to its course",
            )
            raise error

        try:
            with engine.begin() as write:
                # `roster_imports.section_id` is ON DELETE RESTRICT, so the import rows
                # must go first; their files and parsed rows cascade from them.
                write.execute(
                    delete(roster_imports).where(roster_imports.c.section_id == section_id)
                )
                write.execute(delete(enrollments).where(enrollments.c.section_id == section_id))
                write.execute(delete(sections).where(sections.c.section_id == section_id))
                AuditLog(write).record(
                    action=ACTION_SECTION_DELETED,
                    outcome=OUTCOME_SUCCESS,
                    actor=actor_of(caller),
                    target_type="section",
                    target_id=section_id,
                    course_id=course_id,
                    detail=f"deleted class {row.section_code!r} with its roster rows",
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
                # FK order: sections/roster imports are RESTRICT, enrollments cascade from
                # sections, roles cascade from the course, audit rows null out their reference.
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
                    write.execute(
                        delete(enrollments).where(enrollments.c.section_id.in_(section_ids))
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
                        f"with {len(section_ids)} class(es)"
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
