"""Server-side authorization: platform roles plus course scope.

`MAIN/docs/architecture/security.md` §3.6 requires three independent checks
(authenticated / role / scope) and forbids trusting the client for any of them. The
front end hiding a button is not a control, so every handler that writes resolves the
caller's authority from `b4.roles` and `b4.enrollments` on each request.

The role vocabulary is exactly the `Role` enum declared in
`MAIN/contracts/openapi/common.yaml` (`student`, `teacher`, `assistant`, `admin`); B4
does not define a second one.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import Connection, and_, select

from .db.tables import courses, enrollments, roles, sections
from .errors import forbidden
from .security.tokens import CourseScope

ROLE_STUDENT = "student"
ROLE_TEACHER = "teacher"
ROLE_ASSISTANT = "assistant"
ROLE_ADMIN = "admin"
ROLES = (ROLE_STUDENT, ROLE_TEACHER, ROLE_ASSISTANT, ROLE_ADMIN)

#: Roles that carry teaching authority for a specific course.
COURSE_STAFF_ROLES = (ROLE_TEACHER, ROLE_ASSISTANT)


@dataclass(frozen=True)
class Principal:
    """The authenticated caller, resolved from the database rather than from the token.

    The token is trusted for *identity* (it is signed); roles and scope are re-read here
    so that revoking a role takes effect immediately instead of at token expiry.
    """

    user_id: uuid.UUID
    platform_roles: frozenset[str]
    course_roles: dict[uuid.UUID, frozenset[str]]
    student_sections: dict[uuid.UUID, uuid.UUID]

    @property
    def is_admin(self) -> bool:
        return ROLE_ADMIN in self.platform_roles

    @property
    def all_roles(self) -> tuple[str, ...]:
        every = set(self.platform_roles)
        for role_set in self.course_roles.values():
            every.update(role_set)
        return tuple(sorted(every))

    @property
    def scoped_course_ids(self) -> frozenset[uuid.UUID]:
        return frozenset(self.course_roles)

    def has_course_write(self, course_id: uuid.UUID) -> bool:
        if self.is_admin:
            return True
        granted = self.course_roles.get(course_id, frozenset())
        return bool(granted & set(COURSE_STAFF_ROLES))

    def has_course_read(self, course_id: uuid.UUID) -> bool:
        if self.has_course_write(course_id):
            return True
        return any(section_course == course_id for section_course in self.student_sections.values())

    def as_scopes(self) -> tuple[CourseScope, ...]:
        """Course scopes for the access-token claim: staff scope plus own enrollment."""
        entries: dict[uuid.UUID, uuid.UUID | None] = {}
        for course_id in self.course_roles:
            entries.setdefault(course_id, None)
        for section_id, course_id in self.student_sections.items():
            if entries.get(course_id) is None:
                entries[course_id] = section_id
        return tuple(
            CourseScope(
                course_id=str(course_id), section_id=str(section_id) if section_id else None
            )
            for course_id, section_id in sorted(entries.items(), key=lambda item: str(item[0]))
        )


def resolve_principal(connection: Connection, user_id: uuid.UUID) -> Principal:
    """Read the caller's platform roles, course roles and own enrollments."""
    role_rows = connection.execute(
        select(roles.c.role, roles.c.course_id).where(roles.c.user_id == user_id)
    ).fetchall()
    platform: set[str] = set()
    per_course: dict[uuid.UUID, set[str]] = {}
    for role, course_id in role_rows:
        if course_id is None:
            platform.add(str(role))
        else:
            per_course.setdefault(course_id, set()).add(str(role))
    enrollment_rows = connection.execute(
        select(enrollments.c.section_id, sections.c.course_id)
        .select_from(enrollments.join(sections, enrollments.c.section_id == sections.c.section_id))
        .where(and_(enrollments.c.user_id == user_id, enrollments.c.is_active.is_(True)))
    ).fetchall()
    student_sections = dict(enrollment_rows)
    return Principal(
        user_id=user_id,
        platform_roles=frozenset(platform),
        course_roles={key: frozenset(value) for key, value in per_course.items()},
        student_sections=student_sections,
    )


def require_platform_role(principal: Principal, role: str) -> None:
    """Require a platform-wide role; a course-scoped grant never satisfies this."""
    if role not in principal.platform_roles:
        raise forbidden(f"This operation requires the {role} role.")


def require_any_platform_role(principal: Principal, *wanted: str) -> None:
    if not (principal.platform_roles & set(wanted)):
        raise forbidden(f"This operation requires one of: {', '.join(sorted(wanted))}.")


def require_course_write(principal: Principal, course_id: uuid.UUID) -> None:
    if not principal.has_course_write(course_id):
        raise forbidden("Write access to this course is not granted to the caller.")


def require_course_read(principal: Principal, course_id: uuid.UUID) -> None:
    if not principal.has_course_read(course_id):
        raise forbidden("Read access to this course is not granted to the caller.")


def course_id_of_section(connection: Connection, section_id: uuid.UUID) -> uuid.UUID | None:
    """Resolve a section's course, or None when the section does not exist.

    Returning None lets a handler answer `404` after the caller has been authorized to
    know, which keeps "not permitted" (`403`) and "not there" (`404`) distinguishable.
    """
    row = connection.execute(
        select(sections.c.course_id).where(sections.c.section_id == section_id)
    ).fetchone()
    return None if row is None else row[0]


def visible_course_ids(connection: Connection, principal: Principal) -> list[uuid.UUID] | None:
    """Courses the caller may list; `None` means "every active course" (admin)."""
    if principal.is_admin:
        rows = connection.execute(
            select(courses.c.course_id).where(courses.c.is_active.is_(True))
        ).fetchall()
        return [row[0] for row in rows]
    visible = set(principal.course_roles) | set(principal.student_sections.values())
    if not visible:
        return []
    rows = connection.execute(
        select(courses.c.course_id).where(
            and_(courses.c.course_id.in_(visible), courses.c.is_active.is_(True))
        )
    ).fetchall()
    return [row[0] for row in rows]
