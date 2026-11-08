"""Course -> section (课序) -> class (班级) -> roster maintenance (v0.32.0).

The model this module maintains is the one the product actually has:

* a course contains several sections;
* a section contains several classes;
* a class contains students, and an enrollment row is the single fact "this student is
  in this class of this section of this course" (plus the account link once the student
  registered).

Two rules are enforced here and, more importantly, in the database
(`uq_enrollments_course_id_student_number_active` and the composite foreign keys):

* one student has at most one **active** enrollment per course. A roster import, an
  attached registered class, a registration binding and a roster edit all go through this
  module, so a concurrent request cannot create a second active section -- the unique
  index is the final authority and an :class:`IntegrityError` is translated into a clear
  `409` instead of a `500`;
* a class always belongs to the section its enrollments name, so section/class/course can
  never disagree.

Nothing here decides authorization: the API layer resolves the caller's course scope and
passes it in.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Connection, and_, func, select
from sqlalchemy.exc import IntegrityError

from .db.tables import (
    classes,
    courses,
    enrollments,
    registration_allowlist,
    registration_allowlist_batches,
    sections,
    users,
)
from .errors import bad_request, conflict, not_found, unprocessable

MAX_CLASS_NAME_LENGTH = 64

#: Where a class came from. The API exposes the same vocabulary (`ClassPublic.source`).
CLASS_SOURCE_REGISTERED = "registered_class"
CLASS_SOURCE_ROSTER = "roster_import"
CLASS_SOURCE_MANUAL = "manual"
CLASS_SOURCES = (CLASS_SOURCE_REGISTERED, CLASS_SOURCE_ROSTER, CLASS_SOURCE_MANUAL)


def utcnow() -> datetime:
    """Millisecond precision; every `timestamptz(3)` column is written through here."""
    return datetime.now(UTC).replace(microsecond=0)


def file_stem(filename: str) -> str:
    """Class name from a file name: ``01ban.csv`` -> ``01ban`` (same rule everywhere)."""
    stem = filename.strip().rsplit("/", 1)[-1]
    for suffix in (".csv", ".CSV", ".xlsx", ".XLSX"):
        if stem.endswith(suffix):
            return stem[: -len(suffix)].strip() or stem
    return stem


def normalize_class_name(raw: str) -> str:
    candidate = raw.strip()
    if not candidate:
        raise bad_request("The class name must not be blank.")
    if len(candidate) > MAX_CLASS_NAME_LENGTH:
        raise bad_request(f"The class name must be at most {MAX_CLASS_NAME_LENGTH} characters.")
    return candidate


@dataclass(frozen=True)
class RosterConflict:
    """One student already actively enrolled elsewhere in the same course."""

    student_number: str
    section_id: str
    section_code: str
    class_id: str
    class_name: str


def section_row(connection: Connection, section_id: uuid.UUID):
    return connection.execute(
        select(sections).where(sections.c.section_id == section_id)
    ).fetchone()


def class_row(connection: Connection, class_id: uuid.UUID):
    return connection.execute(select(classes).where(classes.c.class_id == class_id)).fetchone()


def section_in_course(
    connection: Connection, *, course_id: uuid.UUID, section_id: uuid.UUID
):
    row = connection.execute(
        select(sections).where(
            and_(sections.c.section_id == section_id, sections.c.course_id == course_id)
        )
    ).fetchone()
    if row is None:
        raise not_found("The section does not exist in this course.")
    return row


def class_in_course(connection: Connection, *, course_id: uuid.UUID, class_id: uuid.UUID):
    """The class row plus its section, or `404`/`422` when the pair is inconsistent."""
    row = connection.execute(
        select(classes, sections)
        .select_from(classes.join(sections, classes.c.section_id == sections.c.section_id))
        .where(classes.c.class_id == class_id)
    ).fetchone()
    if row is None:
        raise not_found("The class does not exist.")
    if row.course_id != course_id:
        raise unprocessable("The class does not belong to this course.")
    return row


# ------------------------------------------------------------------ roster validation
def active_enrollment_conflicts(
    connection: Connection, *, course_id: uuid.UUID, student_numbers: list[str]
) -> list[RosterConflict]:
    """Existing **active** enrollments of the same course for these student numbers.

    The caller decides what a conflict means (reject the import row, reject the whole
    attach request); this function only reports the facts. It never picks a section.
    """
    numbers = sorted({number for number in student_numbers if number})
    if not numbers:
        return []
    rows = connection.execute(
        select(
            enrollments.c.student_number,
            enrollments.c.section_id,
            sections.c.section_code,
            enrollments.c.class_id,
            classes.c.class_name,
        )
        .select_from(
            enrollments.join(sections, enrollments.c.section_id == sections.c.section_id).join(
                classes, enrollments.c.class_id == classes.c.class_id
            )
        )
        .where(
            and_(
                enrollments.c.course_id == course_id,
                enrollments.c.is_active.is_(True),
                enrollments.c.student_number.in_(numbers),
            )
        )
    ).fetchall()
    return [
        RosterConflict(
            student_number=str(row[0]),
            section_id=str(row[1]),
            section_code=str(row[2]),
            class_id=str(row[3]),
            class_name=str(row[4]),
        )
        for row in rows
    ]


def account_name_mismatches(
    connection: Connection, *, rows: list[tuple[str, str]]
) -> list[str]:
    """Student numbers whose existing account name differs from the roster name.

    Binding two differently-named records by student number alone would merge two people,
    so the caller refuses those rows instead of guessing which name is right.
    """
    numbers = sorted({number for number, _name in rows if number})
    if not numbers:
        return []
    account_rows = connection.execute(
        select(users.c.student_number, users.c.display_name).where(
            and_(users.c.student_number.in_(numbers), users.c.is_active.is_(True))
        )
    ).fetchall()
    account_names = {row[0]: str(row[1]).strip() for row in account_rows}
    return [
        number
        for number, name in rows
        if number in account_names and account_names[number] != name.strip()
    ]


def bound_accounts(
    connection: Connection, *, student_numbers: list[str]
) -> dict[str, uuid.UUID]:
    """Student number -> account id for accounts that already exist."""
    numbers = sorted({number for number in student_numbers if number})
    if not numbers:
        return {}
    rows = connection.execute(
        select(users.c.student_number, users.c.user_id).where(
            and_(users.c.student_number.in_(numbers), users.c.is_active.is_(True))
        )
    ).fetchall()
    return {str(row[0]): row[1] for row in rows}


def bind_account_to_enrollments(
    connection: Connection,
    *,
    user_id: uuid.UUID,
    student_number: str,
    display_name: str,
    now: datetime,
) -> tuple[int, int]:
    """Bind one account to every matching active roster row; returns (bound, mismatched).

    This is the **registration** direction of the binding: the account exists and the
    roster rows may have been imported before. A row whose recorded name differs from the
    verified registration name is left unbound and counted, never merged.
    """
    rows = connection.execute(
        select(
            enrollments.c.enrollment_id,
            enrollments.c.student_number,
            enrollments.c.display_name,
            enrollments.c.user_id,
        ).where(
            and_(
                enrollments.c.student_number == student_number,
                enrollments.c.is_active.is_(True),
            )
        )
    ).fetchall()
    bound = 0
    mismatched = 0
    for row in rows:
        if str(row.display_name).strip() != display_name.strip():
            mismatched += 1
            continue
        if row.user_id == user_id:
            continue
        if row.user_id is not None:
            # Another account already claims this roster row: a conflict, not something
            # to overwrite silently.
            mismatched += 1
            continue
        connection.execute(
            enrollments.update()
            .where(enrollments.c.enrollment_id == row.enrollment_id)
            .values(user_id=user_id, updated_at=now)
        )
        bound += 1
    return bound, mismatched


# ------------------------------------------------------------------------ class views
def _class_summary(connection: Connection, class_id: uuid.UUID) -> dict[str, object]:
    row = connection.execute(
        select(
            classes,
            sections,
            func.count(enrollments.c.enrollment_id).label("enrollment_count"),
        )
        .select_from(
            classes.join(sections, classes.c.section_id == sections.c.section_id).outerjoin(
                enrollments,
                and_(
                    enrollments.c.class_id == classes.c.class_id,
                    enrollments.c.is_active.is_(True),
                ),
            )
        )
        .where(classes.c.class_id == class_id)
        .group_by(classes.c.class_id, sections.c.section_id)
    ).fetchone()
    if row is None:  # pragma: no cover - the caller just loaded it
        raise not_found("The class does not exist.")
    bound = connection.execute(
        select(func.count())
        .select_from(enrollments)
        .where(
            and_(
                enrollments.c.class_id == class_id,
                enrollments.c.is_active.is_(True),
                enrollments.c.user_id.is_not(None),
            )
        )
    ).scalar_one()
    return {
        "class_id": str(row.class_id),
        "course_id": str(row.course_id),
        "section_id": str(row.section_id),
        "class_name": row.class_name,
        "source": row.source,
        "allowlist_batch_id": (
            str(row.allowlist_batch_id) if row.allowlist_batch_id is not None else None
        ),
        "enrollment_count": int(row.enrollment_count),
        "bound_count": int(bound),
        "created_at": row.created_at,
    }


def list_class_summaries(connection: Connection, section_id: uuid.UUID) -> list[dict[str, object]]:
    rows = connection.execute(
        select(classes.c.class_id)
        .where(classes.c.section_id == section_id)
        .order_by(classes.c.class_name, classes.c.class_id)
    ).fetchall()
    return [_class_summary(connection, row[0]) for row in rows]


def class_summary(connection: Connection, class_id: uuid.UUID) -> dict[str, object]:
    return _class_summary(connection, class_id)


def find_class_for_batch(
    connection: Connection, *, section_id: uuid.UUID, batch_id: uuid.UUID
) -> dict[str, object] | None:
    """The class that already represents this registered class in this section, if any.

    Used to make a concurrent identical attach idempotent: the loser of the unique-index race
    returns the class the winner created instead of a 500.
    """
    row = connection.execute(
        select(classes.c.class_id).where(
            and_(
                classes.c.section_id == section_id,
                classes.c.allowlist_batch_id == batch_id,
            )
        )
    ).fetchone()
    return None if row is None else _class_summary(connection, row[0])


# ----------------------------------------------------------------------- class writes
def create_class(
    connection: Connection,
    *,
    section_id: uuid.UUID,
    class_name: str | None,
    batch_id: uuid.UUID | None,
    actor_user_id: uuid.UUID,
    now: datetime,
) -> tuple[dict[str, object], bool]:
    """Create one class under a section; returns ``(summary, already_existed)``.

    With `batch_id`, the class starts as a copy of that registered class. Re-attaching the
    same batch to the same section is idempotent (it returns the existing class). Every
    other conflict -- the same class name, any student already actively enrolled in
    another section of this course, an account whose name disagrees with the roster -- is
    a `409` and the whole request rolls back. This function never silently chooses a
    section.
    """
    section = section_row(connection, section_id)
    if section is None:
        raise not_found("The section does not exist.")
    course_id = section.course_id

    batch = None
    entries: list[tuple[str, str]] = []
    if batch_id is not None:
        batch = connection.execute(
            select(registration_allowlist_batches).where(
                registration_allowlist_batches.c.batch_id == batch_id
            )
        ).fetchone()
        if batch is None:
            raise not_found("The registered class does not exist.")
        existing = connection.execute(
            select(classes).where(
                and_(
                    classes.c.section_id == section_id,
                    classes.c.allowlist_batch_id == batch_id,
                )
            )
        ).fetchone()
        if existing is not None:
            return _class_summary(connection, existing.class_id), True
        entries = [
            (str(row[0]), str(row[1]))
            for row in connection.execute(
                select(
                    registration_allowlist.c.student_number,
                    registration_allowlist.c.display_name,
                )
                .where(registration_allowlist.c.batch_id == batch_id)
                .order_by(registration_allowlist.c.student_number)
            ).fetchall()
        ]

    resolved_name = normalize_class_name(
        class_name if class_name is not None else file_stem(batch.filename if batch else "")
    )
    if class_name is None and batch is None:
        raise bad_request("A class name is required when no registered class is referenced.")

    clash = connection.execute(
        select(classes.c.class_id).where(
            and_(classes.c.section_id == section_id, classes.c.class_name == resolved_name)
        )
    ).fetchone()
    if clash is not None:
        raise conflict(f"A class named {resolved_name!r} already exists in this section.")

    if entries:
        _reject_roster_conflicts(connection, course_id=course_id, rows=entries)

    class_id = uuid.uuid4()
    connection.execute(
        classes.insert().values(
            class_id=class_id,
            section_id=section_id,
            class_name=resolved_name,
            source=(
                CLASS_SOURCE_REGISTERED
                if batch is not None
                else CLASS_SOURCE_MANUAL
            ),
            allowlist_batch_id=batch_id,
            created_by=actor_user_id,
            created_at=now,
            updated_at=now,
        )
    )
    if entries:
        accounts = bound_accounts(
            connection, student_numbers=[number for number, _name in entries]
        )
        connection.execute(
            enrollments.insert(),
            [
                {
                    "enrollment_id": uuid.uuid4(),
                    "course_id": course_id,
                    "section_id": section_id,
                    "class_id": class_id,
                    "user_id": accounts.get(number),
                    "student_number": number,
                    "display_name": name,
                    "source": CLASS_SOURCE_REGISTERED,
                    "roster_import_id": None,
                    "is_active": True,
                    "created_at": now,
                    "updated_at": now,
                }
                for number, name in entries
            ],
        )
    return _class_summary(connection, class_id), False


def _reject_roster_conflicts(
    connection: Connection, *, course_id: uuid.UUID, rows: list[tuple[str, str]]
) -> None:
    """Refuse a roster set that would violate the one-active-section-per-course rule."""
    conflicts = active_enrollment_conflicts(
        connection, course_id=course_id, student_numbers=[number for number, _name in rows]
    )
    if conflicts:
        sections_named = sorted({f"{item.section_code}/{item.class_name}" for item in conflicts})
        raise conflict(
            "这些学生已在同一课程的其他课序/班级中有效在册, 无法重复加入: "
            f"冲突学生 {len(conflicts)} 人(已有: {', '.join(sections_named[:5])}). "
            "同一课程一名学生只能属于一个有效课序; 请先从原班级移出后再加入."
        )
    mismatched = account_name_mismatches(connection, rows=rows)
    if mismatched:
        raise conflict(
            f"有 {len(mismatched)} 名学生的账号姓名与名单不一致, 无法绑定账号; "
            "请先修正名单姓名或账号资料后重试."
        )


def rename_class(
    connection: Connection, *, class_id: uuid.UUID, class_name: str, now: datetime
) -> dict[str, object]:
    row = class_row(connection, class_id)
    if row is None:
        raise not_found("The class does not exist.")
    name = normalize_class_name(class_name)
    clash = connection.execute(
        select(classes.c.class_id).where(
            and_(
                classes.c.section_id == row.section_id,
                classes.c.class_name == name,
                classes.c.class_id != class_id,
            )
        )
    ).fetchone()
    if clash is not None:
        raise conflict(f"A class named {name!r} already exists in this section.")
    connection.execute(
        classes.update()
        .where(classes.c.class_id == class_id)
        .values(class_name=name, updated_at=now)
    )
    return _class_summary(connection, class_id)


def delete_class(connection: Connection, *, class_id: uuid.UUID) -> dict[str, object]:
    row = class_row(connection, class_id)
    if row is None:
        raise not_found("The class does not exist.")
    connection.execute(enrollments.delete().where(enrollments.c.class_id == class_id))
    connection.execute(classes.delete().where(classes.c.class_id == class_id))
    return row


# ------------------------------------------------------------------------- roster reads
def course_roster_summary(connection: Connection, course_id: uuid.UUID) -> dict[str, object]:
    """Course-wide audience counts computed in SQL (never summed from a client page).

    Unbound students are counted, not dropped: a roster row without an account is still a
    student who owes work, and the product must say so.
    """
    course = connection.execute(
        select(courses.c.course_id).where(courses.c.course_id == course_id)
    ).fetchone()
    if course is None:
        raise not_found("The course does not exist.")

    section_rows = connection.execute(
        select(sections.c.section_id, sections.c.section_code, sections.c.term)
        .where(sections.c.course_id == course_id)
        .order_by(sections.c.section_code, sections.c.section_id)
    ).fetchall()
    class_rows = connection.execute(
        select(classes.c.class_id, classes.c.section_id, classes.c.class_name)
        .select_from(classes.join(sections, classes.c.section_id == sections.c.section_id))
        .where(sections.c.course_id == course_id)
        .order_by(classes.c.class_name, classes.c.class_id)
    ).fetchall()
    counts = connection.execute(
        select(
            enrollments.c.section_id,
            enrollments.c.class_id,
            func.count().label("total"),
            func.count(enrollments.c.user_id).label("bound"),
        )
        .where(and_(enrollments.c.course_id == course_id, enrollments.c.is_active.is_(True)))
        .group_by(enrollments.c.section_id, enrollments.c.class_id)
    ).fetchall()
    by_class: dict[uuid.UUID, tuple[int, int]] = {
        row[1]: (int(row[2]), int(row[3])) for row in counts
    }

    sections_out: list[dict[str, object]] = []
    total = bound = 0
    for section_row_ in section_rows:
        classes_out = []
        section_total = section_bound = 0
        for class_row_ in class_rows:
            if class_row_.section_id != section_row_.section_id:
                continue
            class_total, class_bound = by_class.get(class_row_.class_id, (0, 0))
            section_total += class_total
            section_bound += class_bound
            classes_out.append(
                {
                    "class_id": str(class_row_.class_id),
                    "class_name": class_row_.class_name,
                    "student_total": class_total,
                    "bound_count": class_bound,
                    "unbound_count": class_total - class_bound,
                }
            )
        total += section_total
        bound += section_bound
        sections_out.append(
            {
                "section_id": str(section_row_.section_id),
                "section_code": section_row_.section_code,
                "term": section_row_.term,
                "class_count": len(classes_out),
                "student_total": section_total,
                "bound_count": section_bound,
                "unbound_count": section_total - section_bound,
                "classes": classes_out,
            }
        )
    return {
        "course_id": str(course_id),
        "section_count": len(section_rows),
        "class_count": len(class_rows),
        "student_total": total,
        "bound_count": bound,
        "unbound_count": total - bound,
        "sections": sections_out,
    }


def my_memberships(connection: Connection, user_id: uuid.UUID) -> list[dict[str, object]]:
    """The caller's own active memberships, with course/section/class labels."""
    rows = connection.execute(
        select(
            courses.c.course_id,
            courses.c.course_code,
            courses.c.title,
            sections.c.section_id,
            sections.c.section_code,
            sections.c.term,
            classes.c.class_id,
            classes.c.class_name,
        )
        .select_from(
            enrollments.join(courses, enrollments.c.course_id == courses.c.course_id)
            .join(sections, enrollments.c.section_id == sections.c.section_id)
            .join(classes, enrollments.c.class_id == classes.c.class_id)
        )
        .where(and_(enrollments.c.user_id == user_id, enrollments.c.is_active.is_(True)))
        .order_by(courses.c.title, sections.c.section_code, classes.c.class_name)
    ).fetchall()
    return [
        {
            "course_id": str(row[0]),
            "course_code": row[1],
            "course_title": row[2],
            "section_id": str(row[3]),
            "section_code": row[4],
            "term": row[5],
            "class_id": str(row[6]),
            "class_name": row[7],
        }
        for row in rows
    ]


def authorization_for_user(connection: Connection, user_id: uuid.UUID) -> dict[str, object] | None:
    """Effective roles and memberships for the internal authorization endpoint.

    Ids only: B3/B2 need to decide access, not to learn identity data. `None` means the
    account does not exist.
    """
    from .db.tables import roles as roles_table

    user = connection.execute(select(users).where(users.c.user_id == user_id)).fetchone()
    if user is None:
        return None
    role_rows = connection.execute(
        select(roles_table.c.role, roles_table.c.course_id).where(
            roles_table.c.user_id == user_id
        )
    ).fetchall()
    # A deactivated account keeps no effective authority at all: the endpoint answers "what
    # may this account reach right now", and a deactivated account may reach nothing.
    platform_roles = (
        sorted({str(row[0]) for row in role_rows if row[1] is None}) if user.is_active else []
    )
    course_grants = (
        sorted({str(row[1]) for row in role_rows if row[1] is not None})
        if user.is_active
        else []
    )
    enrollments_out: list[dict[str, str]] = []
    if user.is_active:
        enrollments_out = [
            {
                "course_id": str(row[0]),
                "section_id": str(row[1]),
                "class_id": str(row[2]),
            }
            for row in connection.execute(
                select(
                    enrollments.c.course_id,
                    enrollments.c.section_id,
                    enrollments.c.class_id,
                )
                .where(
                    and_(
                        enrollments.c.user_id == user_id,
                        enrollments.c.is_active.is_(True),
                    )
                )
                .order_by(enrollments.c.course_id, enrollments.c.section_id)
            ).fetchall()
        ]
    return {
        "user_id": str(user_id),
        "platform_roles": platform_roles,
        "course_grants": course_grants,
        "enrollments": enrollments_out,
    }


def translate_integrity_error(error: IntegrityError) -> Exception:
    """Map the one-active-enrollment unique index to a clear `409`."""
    text = str(getattr(error, "orig", error))
    if "uq_enrollments_course_id_student_number_active" in text:
        return conflict(
            "同一课程中该学生已有另一个有效课序/班级(并发写入被数据库约束拦下); "
            "请刷新后重试, 服务端不会静默选择其中一个课序."
        )
    if "uq_classes_section_id_class_name" in text:
        return conflict("同一课序下已存在同名班级.")
    if "uq_classes_section_id_allowlist_batch_id" in text:
        return conflict("该已注册班级已经加入这个课序.")
    return error
