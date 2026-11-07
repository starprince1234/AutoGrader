"""Assignment maintenance (``RQ-B-3-20``).

An assignment is a course-scoped, **multi-section** set of published question versions,
optionally narrowed to named students inside those sections. Two facts follow and shape
this module:

* referencing a draft is refused with ``409`` (the contract's own wording); the rule is
  checked here and is intentionally *not* a database CHECK, because "is the referenced
  version published?" is a cross-row condition and a trigger would fire on the version's
  own lifecycle updates;
* "does this section belong to this course" and "is this student actively enrolled" are
  owned by the identity/course module, so the target ids are validated through the
  :class:`~autograder_b3.integration.b4.TargetValidator` port before anything is written.
  An unavailable validator refuses creation (``503``) rather than accepting the ids.

Reads are audience-aware: a teacher reads assignments in the courses they are staff on
(with section-scoped tokens restricted to the target sections), a student only reads
assignments whose target sections contain their class and, when the assignment narrows
its audience, whose student list contains them.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from ..auth import (
    READ_ROLES,
    Principal,
    require_assignment_read,
    require_authenticated,
    require_course_write,
)
from ..dto.internal import AssignmentCreateInputModel
from ..errors import (
    AssignmentTargetsInvalid,
    AssignmentTargetsUnavailable,
    DraftAssignmentReference,
    Forbidden,
    NotFound,
)
from ..infra.db import Database
from ..infra.models import Assignment
from ..infra.repositories import QuestionBankRepository
from ..integration.b4 import TargetValidator, TargetsUnavailable
from ..logging import get_logger, log_event


@dataclass(frozen=True)
class AssignmentView:
    """One assignment plus the rows that live outside it.

    The targets are separate tables and the question list is ordered by item ordinal, so
    a plain ``Assignment`` row cannot be serialized on its own. Keeping the three lists
    together here means every caller (create, detail, list) answers from the same shape.
    """

    assignment: Assignment
    question_version_ids: tuple[uuid.UUID, ...]
    section_ids: tuple[uuid.UUID, ...]
    student_ids: tuple[uuid.UUID, ...]


def create_assignment(
    database: Database,
    *,
    principal: Principal | None,
    payload: AssignmentCreateInputModel,
    targets: TargetValidator | None,
    now: datetime,
) -> AssignmentView:
    """Create an assignment with its items and target rows in one transaction.

    Order matters and is the contract's: course write authorization, then the referenced
    versions (``404``/``409``/``422``), then target validation against the identity/course
    module (``422``/``503``), then the write. Nothing is persisted before every check has
    passed, so a refused request leaves no partial assignment behind.
    """
    actor = require_course_write(principal, payload.course_id)
    with database.protected_session() as session:
        bank = QuestionBankRepository(session)
        drafts: list[str] = []
        for version_id in payload.question_version_ids:
            version = bank.get_version(version_id)
            if version is None:
                raise NotFound(f"question version {version_id} not found")
            question = bank.get_question(version.question_id)
            if question is None:  # pragma: no cover
                raise NotFound(f"question {version.question_id} not found")
            if question.course_id != payload.course_id:
                # Cross-course reference: the caller is authorized for
                # payload.course_id, so the *referenced* question is simply not
                # part of this course - refuse rather than leak its existence.
                raise NotFound(f"question version {version_id} not found")
            if version.status != "published":
                drafts.append(str(version_id))
        if drafts:
            raise DraftAssignmentReference(
                "an assignment may only reference published versions; "
                f"draft version(s): {', '.join(sorted(drafts))}"
            )

        _require_valid_targets(targets, payload)

        assignment = bank.create_assignment(
            course_id=payload.course_id,
            title=payload.title,
            due_at=payload.due_at,
            created_by=_actor_id(actor),
            now=now,
        )
        items = [
            bank.add_assignment_item(
                assignment_id=assignment.assignment_id,
                question_version_id=version_id,
                ordinal=index,
                now=now,
            )
            for index, version_id in enumerate(payload.question_version_ids)
        ]
        for section_id in payload.section_ids:
            bank.add_assignment_section(
                assignment_id=assignment.assignment_id, section_id=section_id, now=now
            )
        for student_id in payload.student_ids:
            bank.add_assignment_student(
                assignment_id=assignment.assignment_id, student_id=student_id, now=now
            )
        log_event(
            get_logger("domain.assignments"),
            "assignment.created",
            assignment_id=str(assignment.assignment_id),
            course_id=str(payload.course_id),
            attempt_count=len(items),
            section_count=len(payload.section_ids),
            narrowed_student_count=len(payload.student_ids),
        )
        view = AssignmentView(
            assignment=assignment,
            question_version_ids=tuple(item.question_version_id for item in items),
            section_ids=tuple(sorted(payload.section_ids)),
            student_ids=tuple(sorted(payload.student_ids)),
        )
        session.flush()
        session.expunge(assignment)
        for item in items:
            session.expunge(item)
        return view


def get_assignment_detail(
    database: Database,
    *,
    principal: Principal | None,
    assignment_id: uuid.UUID,
) -> AssignmentView:
    """Read one assignment inside the caller's audience.

    The detail page must not walk the list and match locally: once the list paginates, a
    missing page silently becomes "this assignment does not exist". Absent records are
    ``404``; a record whose audience excludes the caller is ``403`` - the caller does not
    get to learn whether an id exists elsewhere.
    """
    with database.app_session() as session:
        bank = QuestionBankRepository(session)
        assignment = bank.get_assignment(assignment_id)
        if assignment is None:
            raise NotFound(f"assignment {assignment_id} not found")
        sections = [
            row.section_id for row in bank.list_assignment_sections(assignment_id)
        ]
        students = [
            row.student_id for row in bank.list_assignment_students(assignment_id)
        ]
        require_assignment_read(
            principal,
            course_id=assignment.course_id,
            section_ids=sections,
            student_ids=students,
        )
        version_ids = [
            item.question_version_id for item in bank.list_assignment_items(assignment_id)
        ]
        view = AssignmentView(
            assignment=assignment,
            question_version_ids=tuple(version_ids),
            section_ids=tuple(sorted(sections)),
            student_ids=tuple(sorted(students)),
        )
        session.expunge(assignment)
        return view


def list_assignment_versions(
    database: Database,
    *,
    principal: Principal | None,
    course_id: uuid.UUID | None,
    section_id: uuid.UUID | None,
    limit: int,
    after: tuple[datetime, uuid.UUID] | None = None,
) -> tuple[list[AssignmentView], bool]:
    """One keyset page of assignments the caller may read.

    Reader filtering is pushed into the query rather than applied to the fetched page: a
    row dropped after ``LIMIT`` would make the page short and the ``next_cursor`` point
    past rows nobody served. The per-row authorization check still runs as the single
    source of truth for "may this caller read this assignment", so a divergence between
    the SQL prefilter and the policy fails closed.
    """
    actor = require_authenticated(principal)
    visible_courses, visible_sections, audience_student = _reader_scope(actor)
    with database.app_session() as session:
        bank = QuestionBankRepository(session)
        rows, has_more = bank.list_assignments(
            course_ids=actor.readable_courses(),
            course_id=course_id,
            section_id=section_id,
            visible_course_ids=visible_courses,
            visible_section_ids=visible_sections,
            audience_student_id=audience_student,
            limit=limit,
            after=after,
        )
        result: list[AssignmentView] = []
        for row in rows:
            sections = [
                target.section_id for target in bank.list_assignment_sections(row.assignment_id)
            ]
            students = [
                target.student_id for target in bank.list_assignment_students(row.assignment_id)
            ]
            try:
                require_assignment_read(
                    actor,
                    course_id=row.course_id,
                    section_ids=sections,
                    student_ids=students,
                )
            except Forbidden:
                continue
            version_ids = [
                item.question_version_id
                for item in bank.list_assignment_items(row.assignment_id)
            ]
            session.expunge(row)
            result.append(
                AssignmentView(
                    assignment=row,
                    question_version_ids=tuple(version_ids),
                    section_ids=tuple(sorted(sections)),
                    student_ids=tuple(sorted(students)),
                )
            )
        return result, has_more


def _require_valid_targets(
    targets: TargetValidator | None, payload: AssignmentCreateInputModel
) -> None:
    """Ask the identity/course module whether these ids mean what the caller claims.

    Running the check even when no student is named is deliberate: a section id that does
    not belong to the course would silently create an assignment nobody can see, which is
    worse than a ``422``. A missing/unreachable validator is a ``503`` - the ids are never
    accepted on trust.
    """
    if targets is None:
        raise AssignmentTargetsUnavailable(
            "assignment target validation is not configured; this deployment cannot "
            "verify the chosen sections and students"
        )
    try:
        validation = targets.validate_targets(
            course_id=payload.course_id,
            section_ids=payload.section_ids,
            student_ids=payload.student_ids,
        )
    except TargetsUnavailable as error:
        raise AssignmentTargetsUnavailable(str(error)) from error
    invalid_sections = [
        section for section in payload.section_ids if section not in validation.valid_section_ids
    ]
    invalid_students = [
        student for student in payload.student_ids if student not in validation.valid_student_ids
    ]
    if invalid_sections or invalid_students:
        # Counts only: the rejected student ids are enrollment data, and a creation error
        # must not become a way to confirm which students are enrolled.
        raise AssignmentTargetsInvalid(
            "the assignment targets are not valid for this course: "
            f"{len(invalid_sections)} section target(s) and "
            f"{len(invalid_students)} student target(s) were rejected"
        )


def _reader_scope(
    actor: Principal,
) -> tuple[frozenset[uuid.UUID], frozenset[uuid.UUID] | None, uuid.UUID | None]:
    """The SQL prefilter for one reader, matching :func:`require_assignment_read`.

    Returns ``(whole_course_ids, target_section_ids, audience_student_id)``; ``None`` means
    "no restriction on this axis" and is only produced for admins/service callers.
    """
    if actor.is_service or actor.is_admin:
        return frozenset(), None, None
    if not actor.has_role(*READ_ROLES):
        raise Forbidden("the caller's role may not read assignments")
    if actor.has_role("teacher"):
        return frozenset(actor.course_wide), frozenset(actor.section_ids), None
    try:
        subject = uuid.UUID(actor.subject)
    except (ValueError, AttributeError, TypeError) as error:
        raise Forbidden("the caller's subject is not a usable student identifier") from error
    return frozenset(), frozenset(actor.section_ids), subject


def _actor_id(actor: Principal) -> uuid.UUID | None:
    try:
        return uuid.UUID(actor.subject)
    except (ValueError, AttributeError, TypeError):
        return None
