"""Statistics service: consumption, rebuild-by-replay and query.

Three entry points, all sharing ``stats.aggregator``'s accounting:

* :func:`apply_consumed_event` - incremental, called by the event consumer after
  the inbox claims the event;
* :func:`rebuild_statistics` - clear the pre-aggregated tables and recompute from
  the durable inbox, in ``occurred_at`` order. This is the "从事件重放重建统计"
  acceptance (``RQ-B-3-30``);
* :func:`question_statistics` - read model. Without a time window it reads the
  pre-aggregated totals (fast path, suitable for 100k+ events); with
  ``from``/``to`` it recomputes from the inbox rows in that window, paging through
  them so a wide window costs bounded memory instead of loading every row, so a
  filtered answer can never disagree with a full replay.

Statistics are also readable straight from the event stream, which is what makes
"replay yields the same numbers" testable without a second data path.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from collections.abc import Mapping, Sequence
from uuid import UUID

from sqlalchemy.orm import Session

from ..dto.internal import EvaluationCompletedEvent
from ..errors import EventRejected
from ..events.whitelist import event_from_inbox_row
from ..infra.repositories import EventStoreRepository
from .aggregator import (
    Accumulator,
    RebuildReport,
    accumulate,
    apply_event_increment,
    read_totals,
    write_absolute_totals,
)

@dataclass(frozen=True)
class StatisticsResult:
    """Aggregate view for one question (or one version of it)."""

    question_id: UUID
    question_version_id: UUID | None
    attempt_count: int
    pass_rate: float
    pass_known_count: int
    score_distribution: Mapping[str, int]
    failure_category_distribution: Mapping[str, int]
    unbucketed_attempt_count: int
    computed_at: datetime

    def as_dict(self) -> dict[str, object]:
        return {
            "question_id": str(self.question_id),
            "question_version_id": (
                None if self.question_version_id is None else str(self.question_version_id)
            ),
            "attempt_count": self.attempt_count,
            "pass_rate": self.pass_rate,
            "pass_known_count": self.pass_known_count,
            "score_distribution": dict(self.score_distribution),
            "failure_category_distribution": dict(self.failure_category_distribution),
            "unbucketed_attempt_count": self.unbucketed_attempt_count,
        }


def apply_consumed_event(
    session: Session, event: EvaluationCompletedEvent, *, now: datetime
) -> None:
    apply_event_increment(session, event, now=now)


def rebuild_statistics(session: Session, *, now: datetime) -> RebuildReport:
    """Rebuild every aggregate from the durable inbox (idempotent by construction)."""
    store = EventStoreRepository(session)
    rows = store.inbox_rows_in_order()
    events = [event_from_inbox_row(row) for row in rows]
    totals = accumulate(events)
    store.clear_statistics()
    write_absolute_totals(session, totals, now=now)
    return RebuildReport(event_count=len(events), version_count=len(totals), totals=totals)


def statistics_for_versions(
    session: Session,
    *,
    version_ids: Sequence[UUID],
    from_at: datetime | None = None,
    to_at: datetime | None = None,
) -> Accumulator:
    """Aggregate over ``version_ids``; windowed queries recompute from the inbox.

    A windowed query walks the window in keyset pages (``inbox_rows_in_window``), so
    peak memory is one page rather than the whole window. The aggregate itself is still
    an integer accumulation, which is what keeps it order-independent and replayable.
    """
    if from_at is not None or to_at is not None:
        rows = EventStoreRepository(session).inbox_rows_in_window(
            version_ids=list(version_ids), from_at=from_at, to_at=to_at
        )
        totals: dict[UUID, Accumulator] = {}
        for row in rows:
            event = event_from_inbox_row(row)
            totals.setdefault(event.question_version_id, Accumulator()).add(event)
        merged = Accumulator()
        for accumulator in totals.values():
            _merge(merged, accumulator)
        return merged
    totals = read_totals(session, version_ids)
    merged = Accumulator()
    for accumulator in totals.values():
        _merge(merged, accumulator)
    return merged


def build_result(
    *,
    question_id: UUID,
    question_version_id: UUID | None,
    accumulator: Accumulator,
    computed_at: datetime,
) -> StatisticsResult:
    return StatisticsResult(
        question_id=question_id,
        question_version_id=question_version_id,
        attempt_count=accumulator.attempt_count,
        pass_rate=_clamp_pass_rate(accumulator.pass_rate),
        pass_known_count=accumulator.pass_known_count,
        score_distribution=dict(sorted(accumulator.score_buckets.items())),
        failure_category_distribution=dict(sorted(accumulator.failure_categories.items())),
        unbucketed_attempt_count=accumulator.unbucketed_attempt_count,
        computed_at=computed_at,
    )


def _merge(target: Accumulator, source: Accumulator) -> None:
    target.attempt_count += source.attempt_count
    target.pass_known_count += source.pass_known_count
    target.pass_count += source.pass_count
    target.unbucketed_attempt_count += source.unbucketed_attempt_count
    target.score_buckets.update(source.score_buckets)
    target.failure_categories.update(source.failure_categories)
    for attribute in ("first_occurred_at", "last_occurred_at"):
        candidate = getattr(source, attribute)
        if candidate is None:
            continue
        current = getattr(target, attribute)
        if current is None:
            setattr(target, attribute, candidate)
        elif attribute == "first_occurred_at" and candidate < current:
            setattr(target, attribute, candidate)
        elif attribute == "last_occurred_at" and candidate > current:
            setattr(target, attribute, candidate)


def _clamp_pass_rate(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def replay_from_envelopes(
    session: Session, envelopes: Sequence[Mapping[str, object]], *, now: datetime
) -> RebuildReport:
    """Test/ops helper: rebuild from raw envelopes instead of the inbox.

    Uses the same validation and accounting as consumption, so it doubles as the
    "replay is stable" evidence: feeding the same envelopes twice gives the same
    numbers, whichever path is used.
    """
    from ..events.whitelist import parse_evaluation_completed

    events = []
    for envelope in envelopes:
        parsed = parse_evaluation_completed(envelope)
        if parsed is None:  # pragma: no cover - parse never returns None
            raise EventRejected("event could not be parsed")
        events.append(parsed)
    totals = accumulate(events)
    EventStoreRepository(session).clear_statistics()
    write_absolute_totals(session, totals, now=now)
    return RebuildReport(event_count=len(events), version_count=len(totals), totals=totals)
