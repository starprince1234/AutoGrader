"""Assignment maintenance (``RQ-B-3-20``).

Since v0.32.0 an assignment is **course-level**: it belongs to exactly one course and
automatically applies to every section (课序), class (班级) and student of that course,
including the ones added later while it is open. The hand-picked target list is gone --
it was a second, silently diverging answer to "who is this assignment for", and the
authoritative audience is the course's current effective students.

Two rules follow and shape this module:

* referencing a draft is refused with ``409`` (the contract's own wording), and a version
  whose question belongs to another course is reported as absent (``404``). Both are
  checked here; "is the referenced version published?" is a cross-row condition and
  intentionally not a database CHECK.
* publication also hands the audience to B4 (which owns delivery). The hand-off is a
  separate, idempotent step **after** the assignment transaction commits: the assignment
  being published is a fact, so a failed hand-off never rolls it back. The truthful state
  is ``notification_state = unavailable`` and a teacher can retry.

Reads are audience-aware:

* a teacher/admin authorizes from the token role plus the token's course scope
  (``require_course_read`` / ``require_course_write``), the same path as the rest of B3;
* a **student** authorizes from B4's live authorization directory, not from the token's
  ``scope`` snapshot: a student added to a course after sign-in sees its assignments, and a
  student removed from a course stops seeing them immediately. The list filters by that
  advisory's course set **in SQL**, so keyset pages are never left short by a post-filter.
  When the directory cannot answer the request is ``503`` -- never "not a member" (``403``)
  and never an empty list.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from ..auth import (
    WRITE_ROLES,
    Principal,
    require_authenticated,
    require_course_read,
    require_course_write,
)
from ..dto.internal import AssignmentCreateInputModel
from ..errors import (
    AuthorizationUnavailable,
    Conflict,
    DraftAssignmentReference,
    Forbidden,
    NotFound,
    UnprocessableEntity,
)
from ..infra.db import Database
from ..infra.models import Assignment
from ..infra.repositories import QuestionBankRepository
from ..integration.b4 import (
    AssignmentNotifier,
    AuthorizationDirectory,
    NotificationRejected,
    NotificationUnavailable,
    UserAuthorization,
)
from ..integration.b4 import AuthorizationUnavailable as DirectoryUnavailable
from ..logging import get_logger, log_event

#: B4 statuses an explicit notification rejection is mapped back onto. Every one of them is
#: a statement about the request (assignment unknown to B4, conflict, unprocessable body),
#: so repeating the retry would fail identically and the caller must see the real status.
_REJECTION_ERRORS = {
    404: NotFound,
    409: Conflict,
    422: UnprocessableEntity,
}


@dataclass(frozen=True)
class AssignmentView:
    """One assignment plus the rows that live outside it.

    The ordered question list is a separate table, so a plain ``Assignment`` row cannot be
    serialized on its own. ``question_version_ids`` keeps creation order: the 1-based
    position names each answer file (``1.txt``), which is why it must not be re-sorted.
    """

    assignment: Assignment
    question_version_ids: tuple[uuid.UUID, ...]


def create_assignment(
    database: Database,
    *,
    principal: Principal | None,
    payload: AssignmentCreateInputModel,
    notifier: AssignmentNotifier | None,
    now: datetime,
) -> AssignmentView:
    """Create a course-level assignment and hand its audience to B4.

    Order matters and is the contract's: course write authorization, then the referenced
    versions (``404``/``409``/``422``), then the write, then the notification hand-off.
    Nothing is persisted before every check has passed, so a refused request leaves no
    partial assignment behind. The hand-off runs after the write commits and cannot undo
    it -- see ``_schedule_notification``.
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
                # Cross-course reference: the caller is authorized for payload.course_id,
                # so the *referenced* question is simply not part of this course - refuse
                # rather than leak its existence.
                raise NotFound(f"question version {version_id} not found")
            if version.status != "published":
                drafts.append(str(version_id))
        if drafts:
            raise DraftAssignmentReference(
                "an assignment may only reference published versions; "
                f"draft version(s): {', '.join(sorted(drafts))}"
            )

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
        version_ids = tuple(item.question_version_id for item in items)
        log_event(
            get_logger("domain.assignments"),
            "assignment.created",
            assignment_id=str(assignment.assignment_id),
            course_id=str(payload.course_id),
            attempt_count=len(items),
        )
        session.flush()
        session.expunge(assignment)
        for item in items:
            session.expunge(item)
    # The hand-off runs only after the write has committed: publishing is a fact, and a
    # B4 failure must not roll it back (it is recorded as `unavailable` instead).
    return _schedule_notification(
        database,
        view=AssignmentView(assignment=assignment, question_version_ids=version_ids),
        notifier=notifier,
        now=now,
        raise_on_rejection=False,
    )


def retry_assignment_notification(
    database: Database,
    *,
    principal: Principal | None,
    assignment_id: uuid.UUID,
    notifier: AssignmentNotifier | None,
    now: datetime,
) -> AssignmentView:
    """Re-run the publication-notification hand-off for one assignment (teacher retry).

    Authorization is the assignment's course write permission. The call is idempotent on
    B4's side, so repeating it never duplicates recipients or re-sends a message; the
    returned view carries the state this attempt produced.

    A B4 rejection with a declared status (404/409/422) is mapped onto the same B3 status:
    that answer is about this request, and reporting ``200 unavailable`` would hide a
    permanent problem behind a button that never works. A transport/5xx failure instead
    leaves ``notification_state = unavailable`` and answers ``200`` with the assignment,
    because the assignment itself is fine and the retry is the fix.
    """
    with database.app_session() as session:
        bank = QuestionBankRepository(session)
        assignment = bank.get_assignment(assignment_id)
        if assignment is None:
            raise NotFound(f"assignment {assignment_id} not found")
        version_ids = tuple(
            item.question_version_id for item in bank.list_assignment_items(assignment_id)
        )
        session.expunge(assignment)
    require_course_write(principal, assignment.course_id)
    view = AssignmentView(assignment=assignment, question_version_ids=version_ids)
    return _schedule_notification(
        database, view=view, notifier=notifier, now=now, raise_on_rejection=True
    )


def close_assignment(
    database: Database,
    *,
    principal: Principal | None,
    assignment_id: uuid.UUID,
    now: datetime,
) -> AssignmentView:
    """End an assignment so it stops accepting submissions (``RQ-B-3-20``).

    The teacher UI needs this exactly when an assignment was published **without** a
    ``due_at``: nothing would otherwise ever move it out of ``open``. Closing is a real
    admission change rather than a display flag -- B2 admits a submission only while the
    status is ``open`` -- so ending an assignment here is enforced server-side instead of
    being hidden by the client.

    Idempotent: closing a closed assignment returns the same row untouched. ``archived`` is
    a different, stronger state and is refused with ``409`` rather than silently downgraded.
    """
    with database.app_session() as session:
        bank = QuestionBankRepository(session)
        assignment = bank.get_assignment(assignment_id)
        if assignment is None:
            raise NotFound(f"assignment {assignment_id} not found")
        require_course_write(principal, assignment.course_id)
        if assignment.status == "archived":
            raise Conflict(
                "an archived assignment cannot be closed; it already refuses submissions"
            )
        already_closed = assignment.status == "closed"
        version_ids = tuple(
            item.question_version_id for item in bank.list_assignment_items(assignment_id)
        )
        session.expunge(assignment)
    if not already_closed:
        # Writes go through the protected engine: the read/authorization path above only
        # ever holds the application session, same split as the notification hand-off.
        with database.protected_session() as session:
            row = session.get(Assignment, assignment_id)
            if row is None:  # pragma: no cover - the row was read in this request
                raise NotFound(f"assignment {assignment_id} not found")
            QuestionBankRepository(session).close_assignment(row, now=now)
            assignment.status = row.status
            assignment.updated_at = row.updated_at
        log_event(
            get_logger("domain.assignments"),
            "assignment.closed",
            assignment_id=str(assignment_id),
            course_id=str(assignment.course_id),
        )
    return AssignmentView(assignment=assignment, question_version_ids=version_ids)


def get_assignment_detail(
    database: Database,
    *,
    principal: Principal | None,
    assignment_id: uuid.UUID,
    directory: AuthorizationDirectory | None,
) -> AssignmentView:
    """Read one assignment inside the caller's authorization.

    The detail page must not walk the list and match locally: once the list paginates, a
    missing page silently becomes "this assignment does not exist". Absent records are
    ``404``; a record the caller may not read is ``403`` - the caller does not get to learn
    whether an id exists elsewhere.
    """
    with database.app_session() as session:
        bank = QuestionBankRepository(session)
        assignment = bank.get_assignment(assignment_id)
        if assignment is None:
            raise NotFound(f"assignment {assignment_id} not found")
        version_ids = tuple(
            item.question_version_id for item in bank.list_assignment_items(assignment_id)
        )
        session.expunge(assignment)
    _require_read_access(principal, course_id=assignment.course_id, directory=directory)
    return AssignmentView(assignment=assignment, question_version_ids=version_ids)


def list_assignment_versions(
    database: Database,
    *,
    principal: Principal | None,
    course_id: uuid.UUID | None,
    limit: int,
    directory: AuthorizationDirectory | None,
    after: tuple[datetime, uuid.UUID] | None = None,
) -> tuple[list[AssignmentView], bool]:
    """One keyset page of assignments the caller may read.

    The reader's course filter is pushed into the query rather than applied to the fetched
    page: a row dropped after ``LIMIT`` would make the page short and the ``next_cursor``
    point past rows nobody served. ``None`` means "no restriction" and is only produced for
    admins/service callers.
    """
    actor = require_authenticated(principal)
    readable_courses = _reader_courses(actor, directory)
    with database.app_session() as session:
        bank = QuestionBankRepository(session)
        rows, has_more = bank.list_assignments(
            course_ids=readable_courses,
            course_id=course_id,
            limit=limit,
            after=after,
        )
        result: list[AssignmentView] = []
        for row in rows:
            version_ids = [
                item.question_version_id
                for item in bank.list_assignment_items(row.assignment_id)
            ]
            session.expunge(row)
            result.append(
                AssignmentView(
                    assignment=row,
                    question_version_ids=tuple(version_ids),
                )
            )
        return result, has_more


# --------------------------------------------------------------------------
# notification hand-off
# --------------------------------------------------------------------------


def _schedule_notification(
    database: Database,
    *,
    view: AssignmentView,
    notifier: AssignmentNotifier | None,
    now: datetime,
    raise_on_rejection: bool,
) -> AssignmentView:
    """Attempt the hand-off once and persist exactly what happened.

    ``raise_on_rejection`` separates the two callers: at creation any failure is recorded
    as ``unavailable`` and the assignment is returned (201); on an explicit retry a B4
    rejection is mapped back to its status after the state is persisted, because that
    answer is about this request rather than a transient outage.
    """
    assignment = view.assignment
    try:
        if notifier is None:
            raise NotificationUnavailable("assignment notifications are not configured")
        notifier.notify(
            assignment_id=assignment.assignment_id,
            course_id=assignment.course_id,
            title=assignment.title,
            due_at=assignment.due_at,
            published_at=assignment.created_at,
        )
    except NotificationRejected as rejection:
        _set_notification_state(
            database, assignment, state="unavailable", scheduled_at=None, now=now
        )
        log_event(
            get_logger("domain.assignments"),
            "assignment.notification.rejected",
            level=30,
            assignment_id=str(assignment.assignment_id),
            course_id=str(assignment.course_id),
            status_code=rejection.status_code,
        )
        if raise_on_rejection:
            raise _REJECTION_ERRORS[rejection.status_code](
                "the notification service refused the assignment notification "
                f"with HTTP {rejection.status_code}"
            ) from rejection
        return AssignmentView(
            assignment=assignment, question_version_ids=view.question_version_ids
        )
    except NotificationUnavailable as error:
        _set_notification_state(
            database, assignment, state="unavailable", scheduled_at=None, now=now
        )
        log_event(
            get_logger("domain.assignments"),
            "assignment.notification.unavailable",
            level=30,
            assignment_id=str(assignment.assignment_id),
            course_id=str(assignment.course_id),
            reason=type(error).__name__,
        )
    else:
        _set_notification_state(
            database, assignment, state="scheduled", scheduled_at=now, now=now
        )
        log_event(
            get_logger("domain.assignments"),
            "assignment.notification.scheduled",
            assignment_id=str(assignment.assignment_id),
            course_id=str(assignment.course_id),
        )
    return AssignmentView(assignment=assignment, question_version_ids=view.question_version_ids)


def _set_notification_state(
    database: Database,
    assignment: Assignment,
    *,
    state: str,
    scheduled_at: datetime | None,
    now: datetime,
) -> None:
    with database.protected_session() as session:
        row = session.get(Assignment, assignment.assignment_id)
        if row is None:  # pragma: no cover - the row was committed by this request
            raise NotFound(f"assignment {assignment.assignment_id} not found")
        QuestionBankRepository(session).set_assignment_notification(
            row, state=state, scheduled_at=scheduled_at, now=now
        )
        # The caller returns this object to the response builder; keep it in step with the
        # committed row instead of re-reading it in a third transaction.
        assignment.notification_state = row.notification_state
        assignment.notification_scheduled_at = row.notification_scheduled_at
        assignment.updated_at = row.updated_at


# --------------------------------------------------------------------------
# read authorization
# --------------------------------------------------------------------------


def _require_read_access(
    principal: Principal | None,
    *,
    course_id: uuid.UUID,
    directory: AuthorizationDirectory | None,
) -> Principal:
    """Whether this caller may read an assignment of ``course_id``.

    Teacher/admin keeps the existing token-role + token-course-scope path. A student is
    decided from B4's live enrollments; the token's role must still say ``student``, but its
    ``scope`` snapshot is not evidence of membership (v0.32.0).
    """
    actor = require_authenticated(principal)
    if actor.is_service or actor.is_admin:
        return actor
    if actor.has_role(*WRITE_ROLES):
        require_course_read(actor, course_id)
        return actor
    if actor.has_role("student"):
        if not _student_authorization(actor, directory).is_enrolled_in(course_id):
            raise Forbidden(
                "the assignment's course is outside the caller's current enrollment"
            )
        return actor
    raise Forbidden("the caller's role may not read assignments")


def _reader_courses(
    actor: Principal, directory: AuthorizationDirectory | None
) -> frozenset[uuid.UUID] | None:
    """The SQL course filter matching :func:`_require_read_access`; ``None`` = no restriction."""
    if actor.is_service or actor.is_admin:
        return None
    if actor.has_role(*WRITE_ROLES):
        return actor.readable_courses()
    if actor.has_role("student"):
        return _student_authorization(actor, directory).enrolled_course_ids
    raise Forbidden("the caller's role may not read assignments")


def _student_authorization(
    actor: Principal, directory: AuthorizationDirectory | None
) -> UserAuthorization:
    """Ask B4 for the caller's live authorization, translating every outage to ``503``.

    A missing directory is not "no memberships": an unconfigured deployment cannot decide
    student access, so it must fail closed and loudly instead of silently denying everyone.
    """
    if directory is None:
        raise AuthorizationUnavailable(
            "the authorization directory is not configured; this deployment cannot "
            "verify the caller's current course enrollment"
        )
    try:
        user_id = uuid.UUID(actor.subject)
    except (ValueError, AttributeError, TypeError) as error:
        raise Forbidden(
            "the caller's subject is not a usable account identifier"
        ) from error
    try:
        return directory.user_authorization(user_id)
    except DirectoryUnavailable as error:
        raise AuthorizationUnavailable(str(error)) from error


def _actor_id(actor: Principal) -> uuid.UUID | None:
    try:
        return uuid.UUID(actor.subject)
    except (ValueError, AttributeError, TypeError):
        return None
