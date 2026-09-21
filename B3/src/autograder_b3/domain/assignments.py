"""Assignment maintenance (``RQ-B-3-20``).

An assignment is a course/section-scoped set of **published** question versions.
Referencing a draft is refused with ``409`` (the contract's own wording); the rule
is checked here and is intentionally *not* a database CHECK, because "is the
referenced version published?" is a cross-row condition and a trigger would fire
on the version's own lifecycle updates.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from collections.abc import Sequence

from ..auth import Principal, require_course_write, require_course_read
from ..dto.internal import AssignmentCreateInputModel
from ..errors import DraftAssignmentReference, NotFound
from ..infra.db import Database
from ..infra.models import Assignment, AssignmentItem
from ..infra.repositories import QuestionBankRepository
from ..logging import get_logger, log_event


def create_assignment(
    database: Database,
    *,
    principal: Principal | None,
    payload: AssignmentCreateInputModel,
    now: datetime,
) -> tuple[Assignment, list[AssignmentItem]]:
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

        assignment = bank.create_assignment(
            course_id=payload.course_id,
            section_id=payload.section_id,
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
        return assignment, items


def list_assignment_versions(
    database: Database,
    *,
    principal: Principal | None,
    course_id: uuid.UUID | None,
    section_id: uuid.UUID | None,
    limit: int,
) -> tuple[list[tuple[Assignment, list[uuid.UUID]]], bool]:
    with database.app_session() as session:
        bank = QuestionBankRepository(session)
        rows, has_more = bank.list_assignments(
            course_ids=principal.readable_courses() if principal else frozenset(),
            course_id=course_id,
            section_id=section_id,
            limit=limit,
        )
        result: list[tuple[Assignment, list[uuid.UUID]]] = []
        for row in rows:
            require_course_read(principal, row.course_id)
            items = bank.list_assignment_items(row.assignment_id)
            result.append((row, [item.question_version_id for item in items]))
        return result, has_more


def _actor_id(actor: Principal) -> uuid.UUID | None:
    try:
        return uuid.UUID(actor.subject)
    except (ValueError, AttributeError, TypeError):
        return None


def version_ids_of(items: Sequence[AssignmentItem]) -> list[uuid.UUID]:
    return [item.question_version_id for item in items]
