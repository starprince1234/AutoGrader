"""Statistics query service (``RQ-B-3-30``).

The read model is built exclusively from de-identified event data that B3 already
validated. Two data paths, guaranteed to agree:

* no time window -> the pre-aggregated tables (fast, indexed, no event scan);
* ``from``/``to`` -> recomputation from the durable inbox rows inside the window.

Nothing here can reach a student's source code or identity: those values were never
stored, so there is no code path to write.
"""

from __future__ import annotations

import uuid
from datetime import datetime, UTC

from ..auth import Principal, require_course_read, require_statistics_read
from ..errors import NotFound, StatisticsQueryInvalid
from ..infra.db import Database
from ..infra.repositories import QuestionBankRepository
from ..logging import get_logger, log_event
from ..stats.service import StatisticsResult, build_result, statistics_for_versions


def question_statistics(
    database: Database,
    *,
    principal: Principal | None,
    question_id: uuid.UUID,
    from_at: datetime | None = None,
    to_at: datetime | None = None,
    question_version_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> StatisticsResult:
    if from_at is not None and to_at is not None and from_at > to_at:
        raise StatisticsQueryInvalid("`from` must not be later than `to`")
    moment = now or datetime.now(UTC)

    with database.app_session() as session:
        bank = QuestionBankRepository(session)
        question = bank.get_question(question_id)
        if question is None or question.deleted_at is not None:
            raise NotFound("question not found")
        require_course_read(principal, question.course_id)
        require_statistics_read(principal, question.course_id)
        versions = bank.list_versions(question_id)
        if question_version_id is not None:
            versions = [item for item in versions if item.question_version_id == question_version_id]
            if not versions:
                raise NotFound("question version not found for this question")

        version_ids = [item.question_version_id for item in versions]
        accumulator = statistics_for_versions(
            session,
            version_ids=version_ids,
            from_at=from_at,
            to_at=to_at,
        )
        result = build_result(
            question_id=question_id,
            question_version_id=question_version_id,
            accumulator=accumulator,
            computed_at=moment,
        )
        log_event(
            get_logger("domain.statistics"),
            "statistics.queried",
            question_id=str(question_id),
            attempt_count=result.attempt_count,
            outcome="windowed" if (from_at or to_at) else "preaggregated",
        )
        return result
