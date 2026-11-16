"""Assignment maintenance (``RQ-B-3-20``).

Since v0.38.0 an assignment carries an explicit **target**: the sections (课序) it was given
to and, inside those, the individually named students. Both empty means the whole course --
the shape of every assignment created before the target existed, so nothing is backfilled and
nothing is guessed. The earlier course-level model (v0.32.0) is gone: it was a second,
silently diverging answer to "who is this assignment for".

Four rules follow and shape this module:

* referencing a draft is refused with ``409`` (the contract's own wording), and a version
  whose question belongs to another course is reported as absent (``404``). Both are
  checked here; "is the referenced version published?" is a cross-row condition and
  intentionally not a database CHECK.
* a target id is checked against the identity/course module **before** anything is written
  (``_confirm_target``): sections and enrollments are B4's facts, so a forged target is a
  ``422`` carrying B4's own detail rather than a stored audience nobody verified. When that
  check cannot run the write is refused with ``503`` -- never admitted unchecked.
* publication also hands the audience to B4 (which owns delivery), and the target travels
  with it so B4 narrows its roster snapshot with the same filter the submission path applies.
  The hand-off is a separate, idempotent step **after** the assignment transaction commits:
  the assignment being published is a fact, so a failed hand-off never rolls it back. The
  truthful state is ``notification_state = unavailable`` and a teacher can retry.
* a target is a **filter over the current roster, not a snapshot**: it is stored as ids and
  resolved against live enrollment whenever somebody reads or submits, so a student who
  leaves stops matching without anybody editing the assignment.

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
from ..dto.internal import AssignmentCreateInputModel, AssignmentTargetInputModel
from ..errors import (
    AuthorizationUnavailable,
    Conflict,
    DraftAssignmentReference,
    Forbidden,
    NotFound,
    TargetInvalid,
    TargetValidationUnavailable,
    UnprocessableEntity,
)
from ..infra.db import Database
from ..infra.models import Assignment
from ..infra.repositories import QuestionBankRepository
from ..integration.b4 import (
    AssignmentNotifier,
    AuthorizationDirectory,
    CourseTargetValidator,
    NotificationRejected,
    NotificationUnavailable,
    TargetRejected,
    UserAuthorization,
)
from ..integration.b4 import AuthorizationUnavailable as DirectoryUnavailable
from ..integration.b4 import TargetValidationUnavailable as TargetCheckUnavailable
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

    The ordered question list and the target are separate tables, so a plain ``Assignment``
    row cannot be serialized on its own. ``question_version_ids`` keeps creation order: the
    1-based position names each answer file (``1.txt``), which is why it must not be
    re-sorted. ``section_ids``/``student_ids`` are the stored target in the order the
    repository returns it; empty tuples mean the whole course, never "unknown".
    """

    assignment: Assignment
    question_version_ids: tuple[uuid.UUID, ...]
    section_ids: tuple[uuid.UUID, ...] = ()
    student_ids: tuple[uuid.UUID, ...] = ()

    @property
    def target(self) -> tuple[tuple[uuid.UUID, ...], tuple[uuid.UUID, ...]]:
        """``(section_ids, student_ids)`` for the serializer, which takes both explicitly."""
        return self.section_ids, self.student_ids


@dataclass(frozen=True)
class ReaderScope:
    """What the SQL reader filter needs for one caller (v0.38.0).

    ``course_ids is None`` means "no restriction" (admin/service). For a student the target
    axes travel too: the assignment must be one they are inside -- a named student, or a
    student of one of its target sections, or any student of the course when the assignment
    names no target at all. Keeping the three facts together is what stops the list and the
    detail read from applying two different versions of "may this student see it".
    """

    course_ids: frozenset[uuid.UUID] | None
    student_id: uuid.UUID | None = None
    student_sections: frozenset[uuid.UUID] | None = None


def create_assignment(
    database: Database,
    *,
    principal: Principal | None,
    payload: AssignmentCreateInputModel,
    notifier: AssignmentNotifier | None,
    targets: CourseTargetValidator | None,
    now: datetime,
) -> AssignmentView:
    """Create an assignment with its target and hand the audience to B4.

    Order matters: course write authorization, the **local** version checks
    (``404``/``409``/``422``), the **confirmed** target, then the write, then the notification
    hand-off. Nothing is persisted before every check has passed, so a refused request leaves
    no partial assignment behind -- including one refused because its target does not belong to
    the course. The hand-off runs after the write commits and cannot undo it -- see
    ``_schedule_notification``.

    The local checks run first on purpose: a draft reference is decided from rows B3 already
    owns, and asking the identity module before it would turn a real ``409`` into a ``503``
    whenever that service is down -- an outage of one dependency reported as a refusal of a
    request it never saw.
    """
    actor = require_course_write(principal, payload.course_id)
    with database.app_session() as session:
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

    # Confirmed before the write transaction opens: a target B4 refuses must not have written a
    # single row, and a remote call inside a database transaction would hold that transaction
    # open for as long as the other service takes to answer.
    section_ids, student_ids = _confirm_target(
        targets, course_id=payload.course_id, target=payload.target
    )
    with database.protected_session() as session:
        bank = QuestionBankRepository(session)
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
        if section_ids or student_ids:
            # Only when the target narrows something: a brand-new assignment has no rows to
            # add, and "no rows" is exactly how the whole course is stored (there is no sentinel
            # row, and the migration deliberately has no "at least one section" constraint --
            # that was the v0.31 model). Insert-only, because the internal role has no DELETE
            # grant on the target tables (migration 0012).
            bank.add_assignment_targets(
                assignment_id=assignment.assignment_id,
                section_ids=section_ids,
                student_ids=student_ids,
                now=now,
            )
        version_ids = tuple(item.question_version_id for item in items)
        log_event(
            get_logger("domain.assignments"),
            "assignment.created",
            assignment_id=str(assignment.assignment_id),
            course_id=str(payload.course_id),
            attempt_count=len(items),
            target_section_count=len(section_ids),
            target_student_count=len(student_ids),
        )
        session.flush()
        session.expunge(assignment)
        for item in items:
            session.expunge(item)
    # The hand-off runs only after the write has committed: publishing is a fact, and a
    # B4 failure must not roll it back (it is recorded as `unavailable` instead).
    return _schedule_notification(
        database,
        view=AssignmentView(
            assignment=assignment,
            question_version_ids=version_ids,
            section_ids=section_ids,
            student_ids=student_ids,
        ),
        notifier=notifier,
        now=now,
        raise_on_rejection=False,
    )


def _confirm_target(
    validator: CourseTargetValidator | None,
    *,
    course_id: uuid.UUID,
    target: AssignmentTargetInputModel | None,
) -> tuple[tuple[uuid.UUID, ...], tuple[uuid.UUID, ...]]:
    """Have the identity/course module confirm the target, or refuse the write.

    An empty target needs no call: "no narrowing" is the whole course, which is a fact about
    the assignment rather than about the roster, so there is nothing to confirm. Any other
    target is confirmed **before** the assignment row exists -- a stored but unverified target
    is either wider than the teacher chose or narrower than the request, and neither is
    visible after the fact.

    Three outcomes, each its own statement:

    * ``422`` (:class:`~autograder_b3.errors.TargetInvalid`) -- the course exists and some id
      is not part of it; the detail is B4's, so it names the offending ids;
    * ``404`` -- the course itself does not exist, which is not the same problem and sends the
      caller to a different fix;
    * ``503`` (:class:`~autograder_b3.errors.TargetValidationUnavailable`) -- the check could
      not run, so nothing was decided and nothing is written. Reading that as "empty" would
      silently widen the audience to the whole course, the one outcome nobody would notice.
    """
    section_ids = () if target is None else tuple(target.section_ids)
    student_ids = () if target is None else tuple(target.student_ids)
    if not section_ids and not student_ids:
        return (), ()
    if validator is None:
        raise TargetValidationUnavailable(
            "the course target validator is not configured; this deployment cannot confirm "
            "that the assignment's target belongs to the course"
        )
    try:
        confirmed = validator.validate_course_target(
            course_id=course_id, section_ids=section_ids, student_ids=student_ids
        )
    except TargetRejected as rejection:
        if rejection.status_code == 404:
            raise NotFound(f"course {course_id} not found") from rejection
        raise TargetInvalid(rejection.detail) from rejection
    except TargetCheckUnavailable as error:
        raise TargetValidationUnavailable(str(error)) from error
    return confirmed.section_ids, confirmed.student_ids


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
        section_ids, student_ids = bank.assignment_targets(assignment_id)
        session.expunge(assignment)
    require_course_write(principal, assignment.course_id)
    view = AssignmentView(
        assignment=assignment,
        question_version_ids=version_ids,
        section_ids=section_ids,
        student_ids=student_ids,
    )
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
        section_ids, student_ids = bank.assignment_targets(assignment_id)
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
    return AssignmentView(
        assignment=assignment,
        question_version_ids=version_ids,
        section_ids=section_ids,
        student_ids=student_ids,
    )


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
        section_ids, student_ids = bank.assignment_targets(assignment_id)
        session.expunge(assignment)
    _require_read_access(
        principal,
        course_id=assignment.course_id,
        directory=directory,
        section_ids=section_ids,
        student_ids=student_ids,
    )
    return AssignmentView(
        assignment=assignment,
        question_version_ids=version_ids,
        section_ids=section_ids,
        student_ids=student_ids,
    )


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

    The reader's scope -- the readable courses and, for a student, the target axes -- is
    pushed into the query rather than applied to the fetched page: a row dropped after
    ``LIMIT`` would make the page short and the ``next_cursor`` point past rows nobody served.
    ``None`` for the course set means "no restriction" and is only produced for admins/service
    callers.
    """
    actor = require_authenticated(principal)
    scope = _reader_scope(actor, directory)
    with database.app_session() as session:
        bank = QuestionBankRepository(session)
        rows, has_more = bank.list_assignments(
            course_ids=scope.course_ids,
            course_id=course_id,
            limit=limit,
            after=after,
            student_id=scope.student_id,
            student_sections=scope.student_sections,
        )
        result: list[AssignmentView] = []
        # Two grouped reads for the whole page instead of two per row: the target is a set of
        # rows in its own tables, and a 200-row page must not turn into 400 statements.
        targets = bank.assignment_targets_bulk([row.assignment_id for row in rows])
        for row in rows:
            version_ids = [
                item.question_version_id
                for item in bank.list_assignment_items(row.assignment_id)
            ]
            section_ids, student_ids = targets.get(row.assignment_id, ((), ()))
            session.expunge(row)
            result.append(
                AssignmentView(
                    assignment=row,
                    question_version_ids=tuple(version_ids),
                    section_ids=section_ids,
                    student_ids=student_ids,
                )
            )
        return result, has_more


def get_assignment_targets(
    database: Database, *, assignment_id: uuid.UUID
) -> tuple[tuple[uuid.UUID, ...], tuple[uuid.UUID, ...]]:
    """该作业的目标课序与指定学生（v0.38.0，迁移 0012）。

    空元组 = 全课程 / 目标课序全体；历史作业（从未选过目标）天然就是空元组，
    因此不需要回填，也不能据此猜测归属。读取只按 assignment_id，不校验调用者——
    授权由调用方（internal endpoint 的 serviceToken 与 public 读取路径）各自完成。
    """
    with database.protected_session() as session:
        return QuestionBankRepository(session).assignment_targets(assignment_id)


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
            # The target narrows the roster snapshot on B4's side, so "who was told" and
            # "who may submit" are computed by the same filter rather than by two.
            section_ids=view.section_ids,
            student_ids=view.student_ids,
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
        return _view_with_notification_state(view)
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
    return _view_with_notification_state(view)


def _view_with_notification_state(view: AssignmentView) -> AssignmentView:
    """The same view after ``_set_notification_state`` mutated the row in place.

    Rebuilding it (instead of re-reading the row) keeps the target and the ordered question
    list that were already loaded, so a hand-off never costs another round trip -- and never
    returns an assignment whose ``target`` looks empty because a second read forgot it.
    """
    return AssignmentView(
        assignment=view.assignment,
        question_version_ids=view.question_version_ids,
        section_ids=view.section_ids,
        student_ids=view.student_ids,
    )


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
    section_ids: tuple[uuid.UUID, ...] = (),
    student_ids: tuple[uuid.UUID, ...] = (),
) -> Principal:
    """Whether this caller may read one assignment of ``course_id``.

    Teacher/admin keeps the existing token-role + token-course-scope path: the target narrows
    who the assignment is *for*, not what its staff may read. A student is decided from B4's
    live enrollments -- the token's role must still say ``student``, but its ``scope`` snapshot
    is not evidence of membership -- **and** from the assignment's target: a student outside
    the target sections (or not among the named students) is refused, so the list and the
    detail page agree instead of one showing a row the other will not open.
    """
    actor = require_authenticated(principal)
    if actor.is_service or actor.is_admin:
        return actor
    if actor.has_role(*WRITE_ROLES):
        require_course_read(actor, course_id)
        return actor
    if actor.has_role("student"):
        authorization = _student_authorization(actor, directory)
        if not authorization.is_enrolled_in(course_id):
            raise Forbidden(
                "the assignment's course is outside the caller's current enrollment"
            )
        if not _target_admits_student(
            actor,
            authorization,
            course_id=course_id,
            section_ids=section_ids,
            student_ids=student_ids,
        ):
            raise Forbidden(
                "the assignment's target does not include the caller"
            )
        return actor
    raise Forbidden("the caller's role may not read assignments")


def _reader_scope(
    actor: Principal, directory: AuthorizationDirectory | None
) -> ReaderScope:
    """The SQL filter matching :func:`_require_read_access`; ``None`` courses = no restriction."""
    if actor.is_service or actor.is_admin:
        return ReaderScope(course_ids=None)
    if actor.has_role(*WRITE_ROLES):
        return ReaderScope(course_ids=actor.readable_courses())
    if actor.has_role("student"):
        authorization = _student_authorization(actor, directory)
        student_id = _actor_id(actor)
        if student_id is None:
            # The named-student axis cannot be evaluated without an account id, and "no id"
            # must never read as "no narrowing" -- that would widen the audience.
            raise Forbidden("the caller's subject is not a usable account identifier")
        return ReaderScope(
            course_ids=authorization.enrolled_course_ids,
            student_id=student_id,
            student_sections=frozenset(
                enrollment.section_id for enrollment in authorization.enrollments
            ),
        )
    raise Forbidden("the caller's role may not read assignments")


def _target_admits_student(
    actor: Principal,
    authorization: UserAuthorization,
    *,
    course_id: uuid.UUID,
    section_ids: tuple[uuid.UUID, ...],
    student_ids: tuple[uuid.UUID, ...],
) -> bool:
    """Whether the assignment's target includes this student (v0.38.0).

    Empty axes mean "not narrowed", so an assignment with no target admits every enrolled
    student -- which is exactly how every assignment created before the target existed reads.
    A named-student axis is checked against the caller's own account id, a section axis against
    the sections they are currently enrolled in **for this course**.
    """
    if student_ids:
        student_id = _actor_id(actor)
        if student_id is None or student_id not in student_ids:
            return False
    if section_ids:
        held = {
            enrollment.section_id
            for enrollment in authorization.enrollments
            if enrollment.course_id == course_id
        }
        if not held & set(section_ids):
            return False
    return True


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
