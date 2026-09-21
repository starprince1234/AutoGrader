"""Statistics aggregation (``RQ-B-3-30``).

Design notes that matter for the acceptance criteria:

* **One accounting function.** Consumption, windowed queries and replay all add
  events through :meth:`Accumulator.add`. Incremental consumption therefore
  cannot drift from a rebuild.
* **Order independence.** Every counter is an integer and every operation is a
  commutative addition, so replaying the stream in ``occurred_at`` order (or in
  any other order) produces identical values - the "统计可重放" acceptance.
  No float accumulators are kept; score information lives only in bucket counts.
* **De-identified by construction.** The inputs are
  :class:`~autograder_b3.dto.internal.EvaluationCompletedEvent`, which has no
  identity field to aggregate even if someone wanted to.

A deliberate non-feature: no distinct-student count. It is not computable from a
payload that forbids ``student_id`` and the frozen ``QuestionStatistics`` DTO has
no field for it (``CHG-B3-0003``).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, UTC
from collections.abc import Iterable, Mapping, Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ..dto.internal import EvaluationCompletedEvent
from ..infra.models import (
    QuestionVersionFailureCategory,
    QuestionVersionScoreBucket,
    QuestionVersionStats,
)
from .buckets import score_bucket_label


@dataclass
class Accumulator:
    """Integer counters for one question version."""

    attempt_count: int = 0
    pass_known_count: int = 0
    pass_count: int = 0
    unbucketed_attempt_count: int = 0
    score_buckets: Counter[str] = field(default_factory=Counter)
    failure_categories: Counter[str] = field(default_factory=Counter)
    first_occurred_at: datetime | None = None
    last_occurred_at: datetime | None = None

    def add(self, event: EvaluationCompletedEvent) -> None:
        self.attempt_count += 1
        if event.is_pass is not None:
            self.pass_known_count += 1
            if event.is_pass:
                self.pass_count += 1
        label = score_bucket_label(
            score=event.score, max_score=event.max_score, declared_bucket=event.score_bucket
        )
        if label is None:
            self.unbucketed_attempt_count += 1
        else:
            self.score_buckets[label] += 1
        if event.failed_category:
            self.failure_categories[event.failed_category] += 1
        occurred = event.occurred_at.astimezone(UTC)
        if self.first_occurred_at is None or occurred < self.first_occurred_at:
            self.first_occurred_at = occurred
        if self.last_occurred_at is None or occurred > self.last_occurred_at:
            self.last_occurred_at = occurred

    @property
    def pass_rate(self) -> float:
        """Passed / attempts whose outcome is known; 0.0 when nothing is known."""
        if self.pass_known_count == 0:
            return 0.0
        return self.pass_count / self.pass_known_count

    def to_dict(self) -> dict[str, object]:
        return {
            "attempt_count": self.attempt_count,
            "pass_known_count": self.pass_known_count,
            "pass_count": self.pass_count,
            "unbucketed_attempt_count": self.unbucketed_attempt_count,
            "score_buckets": dict(self.score_buckets),
            "failure_categories": dict(self.failure_categories),
        }


def accumulate(
    events: Iterable[EvaluationCompletedEvent],
) -> dict[UUID, Accumulator]:
    totals: dict[UUID, Accumulator] = {}
    for event in events:
        totals.setdefault(event.question_version_id, Accumulator()).add(event)
    return totals


def accumulate_one(event: EvaluationCompletedEvent) -> Accumulator:
    accumulator = Accumulator()
    accumulator.add(event)
    return accumulator


def apply_event_increment(
    session: Session, event: EvaluationCompletedEvent, *, now: datetime
) -> None:
    """Add one accepted event to the pre-aggregated tables (idempotent upstream)."""
    delta = accumulate_one(event)
    stats = QuestionVersionStats
    statement = pg_insert(stats).values(
        question_version_id=event.question_version_id,
        attempt_count=delta.attempt_count,
        pass_known_count=delta.pass_known_count,
        pass_count=delta.pass_count,
        unbucketed_attempt_count=delta.unbucketed_attempt_count,
        first_occurred_at=delta.first_occurred_at,
        last_occurred_at=delta.last_occurred_at,
        updated_at=now,
    )
    session.execute(
        statement.on_conflict_do_update(
            index_elements=[stats.question_version_id],
            set_={
                "attempt_count": stats.attempt_count + statement.excluded.attempt_count,
                "pass_known_count": stats.pass_known_count + statement.excluded.pass_known_count,
                "pass_count": stats.pass_count + statement.excluded.pass_count,
                "unbucketed_attempt_count": (
                    stats.unbucketed_attempt_count + statement.excluded.unbucketed_attempt_count
                ),
                "first_occurred_at": func.least(
                    func.coalesce(stats.first_occurred_at, statement.excluded.first_occurred_at),
                    statement.excluded.first_occurred_at,
                ),
                "last_occurred_at": func.greatest(
                    func.coalesce(stats.last_occurred_at, statement.excluded.last_occurred_at),
                    statement.excluded.last_occurred_at,
                ),
                "updated_at": now,
            },
        )
    )

    bucket = QuestionVersionScoreBucket
    for label, count in delta.score_buckets.items():
        bucket_statement = pg_insert(bucket).values(
            question_version_id=event.question_version_id, bucket_label=label, bucket_count=count
        )
        session.execute(
            bucket_statement.on_conflict_do_update(
                index_elements=[bucket.question_version_id, bucket.bucket_label],
                set_={"bucket_count": bucket.bucket_count + bucket_statement.excluded.bucket_count},
            )
        )

    category = QuestionVersionFailureCategory
    for name, count in delta.failure_categories.items():
        category_statement = pg_insert(category).values(
            question_version_id=event.question_version_id,
            failed_category=name,
            category_count=count,
        )
        session.execute(
            category_statement.on_conflict_do_update(
                index_elements=[category.question_version_id, category.failed_category],
                set_={
                    "category_count": category.category_count
                    + category_statement.excluded.category_count
                },
            )
        )
    session.flush()


@dataclass(frozen=True)
class RebuildReport:
    event_count: int
    version_count: int
    totals: Mapping[UUID, Accumulator]

    def as_dict(self) -> dict[str, object]:
        return {
            "event_count": self.event_count,
            "version_count": self.version_count,
            "attempt_count": sum(item.attempt_count for item in self.totals.values()),
        }


def write_absolute_totals(
    session: Session, totals: Mapping[UUID, Accumulator], *, now: datetime
) -> None:
    """Overwrite the pre-aggregated tables with absolute values."""
    for version_id, accumulator in totals.items():
        stats = QuestionVersionStats
        statement = pg_insert(stats).values(
            question_version_id=version_id,
            attempt_count=accumulator.attempt_count,
            pass_known_count=accumulator.pass_known_count,
            pass_count=accumulator.pass_count,
            unbucketed_attempt_count=accumulator.unbucketed_attempt_count,
            first_occurred_at=accumulator.first_occurred_at,
            last_occurred_at=accumulator.last_occurred_at,
            updated_at=now,
        )
        session.execute(
            statement.on_conflict_do_update(
                index_elements=[stats.question_version_id],
                set_={
                    "attempt_count": statement.excluded.attempt_count,
                    "pass_known_count": statement.excluded.pass_known_count,
                    "pass_count": statement.excluded.pass_count,
                    "unbucketed_attempt_count": statement.excluded.unbucketed_attempt_count,
                    "first_occurred_at": statement.excluded.first_occurred_at,
                    "last_occurred_at": statement.excluded.last_occurred_at,
                    "updated_at": now,
                },
            )
        )
        bucket = QuestionVersionScoreBucket
        for label, count in accumulator.score_buckets.items():
            bucket_statement = pg_insert(bucket).values(
                question_version_id=version_id, bucket_label=label, bucket_count=count
            )
            session.execute(
                bucket_statement.on_conflict_do_update(
                    index_elements=[bucket.question_version_id, bucket.bucket_label],
                    set_={"bucket_count": bucket_statement.excluded.bucket_count},
                )
            )
        category = QuestionVersionFailureCategory
        for name, count in accumulator.failure_categories.items():
            category_statement = pg_insert(category).values(
                question_version_id=version_id, failed_category=name, category_count=count
            )
            session.execute(
                category_statement.on_conflict_do_update(
                    index_elements=[category.question_version_id, category.failed_category],
                    set_={"category_count": category_statement.excluded.category_count},
                )
            )
    session.flush()


def read_totals(session: Session, version_ids: Sequence[UUID]) -> dict[UUID, Accumulator]:
    """Read the pre-aggregated totals (fast path when no time window is requested)."""
    if not version_ids:
        return {}
    ids = list(version_ids)
    totals: dict[UUID, Accumulator] = {}
    for row in session.execute(
        select(QuestionVersionStats).where(QuestionVersionStats.question_version_id.in_(ids))
    ).scalars():
        accumulator = Accumulator(
            attempt_count=row.attempt_count,
            pass_known_count=row.pass_known_count,
            pass_count=row.pass_count,
            unbucketed_attempt_count=row.unbucketed_attempt_count,
            first_occurred_at=row.first_occurred_at,
            last_occurred_at=row.last_occurred_at,
        )
        totals[row.question_version_id] = accumulator
    for row in session.execute(
        select(QuestionVersionScoreBucket).where(
            QuestionVersionScoreBucket.question_version_id.in_(ids)
        )
    ).scalars():
        totals.setdefault(row.question_version_id, Accumulator()).score_buckets[
            row.bucket_label
        ] = row.bucket_count
    for row in session.execute(
        select(QuestionVersionFailureCategory).where(
            QuestionVersionFailureCategory.question_version_id.in_(ids)
        )
    ).scalars():
        totals.setdefault(row.question_version_id, Accumulator()).failure_categories[
            row.failed_category
        ] = row.category_count
    return totals
