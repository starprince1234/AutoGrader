"""TC-STT-01..04 and RQ-B-3-99: de-identified statistics from the event stream.

The privacy boundary is a type-level fact (the event DTO has no identity field), so
these tests concentrate on the other three properties: deduplication, replay
stability and correct aggregation - plus the whitelist that keeps identity out.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from autograder_b3.events.consumer import ConsumptionOutcome, EvaluationCompletedConsumer
from autograder_b3.events.whitelist import (
    ALLOWED_PAYLOAD_FIELDS,
    FORBIDDEN_PAYLOAD_FIELDS,
    parse_evaluation_completed,
)
from autograder_b3.domain.statistics import question_statistics
from autograder_b3.errors import StatisticsWindowTooLarge
from autograder_b3.stats.aggregator import Accumulator
from autograder_b3.stats.service import rebuild_statistics

from .conftest import draft_payload, event_envelope
from .support import create_published_question, teacher_principal


@pytest.fixture
def published_version(database, settings):
    question, version = create_published_question(database, settings, payload=draft_payload())
    return question, version


def consume_all(consumer, envelopes):
    return [consumer.handle(envelope) for envelope in envelopes]


def test_attempt_count_pass_rate_and_distributions(database, settings, published_version, clock):
    _question, version = published_version
    consumer = EvaluationCompletedConsumer(database, clock=clock)
    envelopes = [
        event_envelope(question_version_id=version.question_version_id, is_pass=True, score=90),
        event_envelope(question_version_id=version.question_version_id, is_pass=True, score=70),
        event_envelope(
            question_version_id=version.question_version_id,
            is_pass=False,
            score=30,
            failed_category="wrong_output",
        ),
        event_envelope(
            question_version_id=version.question_version_id,
            is_pass=False,
            score=10,
            failed_category="timeout",
        ),
    ]
    results = consume_all(consumer, envelopes)
    assert [item.outcome for item in results] == [ConsumptionOutcome.ACCEPTED] * 4

    result = question_statistics(
        database,
        principal=teacher_principal(),
        question_id=_question.question_id,
        now=clock(),
    )
    assert result.attempt_count == 4
    assert result.pass_known_count == 4
    assert result.pass_rate == pytest.approx(0.5)
    # 20-point bands over score/max_score (docs/database/schema.md, stats.buckets)
    assert result.score_distribution == {"0-19": 1, "20-39": 1, "60-79": 1, "80-100": 1}
    assert result.failure_category_distribution == {"wrong_output": 1, "timeout": 1}
    assert result.unbucketed_attempt_count == 0


def test_pass_rate_ignores_events_without_an_outcome(database, settings, published_version, clock):
    _question, version = published_version
    consumer = EvaluationCompletedConsumer(database, clock=clock)
    consumer.handle(
        event_envelope(question_version_id=version.question_version_id, is_pass=None, score=50)
    )
    consumer.handle(
        event_envelope(question_version_id=version.question_version_id, is_pass=True, score=60)
    )
    result = question_statistics(
        database, principal=teacher_principal(), question_id=_question.question_id, now=clock()
    )
    assert result.attempt_count == 2
    assert result.pass_known_count == 1
    assert result.pass_rate == pytest.approx(1.0)
    assert result.unbucketed_attempt_count == 0


def test_attempts_without_a_max_score_are_unbucketed_not_guessed(
    database, settings, published_version, clock
):
    _question, version = published_version
    consumer = EvaluationCompletedConsumer(database, clock=clock)
    consumer.handle(
        event_envelope(
            question_version_id=version.question_version_id, score=86, max_score=None
        )
    )
    result = question_statistics(
        database, principal=teacher_principal(), question_id=_question.question_id, now=clock()
    )
    assert result.attempt_count == 1
    assert result.unbucketed_attempt_count == 1
    assert result.score_distribution == {}


def test_explicit_score_bucket_from_the_event_wins(database, settings, published_version, clock):
    _question, version = published_version
    consumer = EvaluationCompletedConsumer(database, clock=clock)
    consumer.handle(
        event_envelope(
            question_version_id=version.question_version_id,
            score=None,
            max_score=None,
            extra_payload={"score_bucket": "60-79"},
        )
    )
    result = question_statistics(
        database, principal=teacher_principal(), question_id=_question.question_id, now=clock()
    )
    assert result.score_distribution == {"60-79": 1}
    assert result.unbucketed_attempt_count == 0


# --- TC-STT-01: duplicate deliveries ------------------------------------


def test_duplicate_event_id_is_counted_once(database, settings, published_version, clock, clean_event_store):
    _question, version = published_version
    consumer = EvaluationCompletedConsumer(database, clock=clock)
    envelope = event_envelope(question_version_id=version.question_version_id, is_pass=True)
    outcomes = [consumer.handle(envelope).outcome for _ in range(5)]
    assert outcomes[0] is ConsumptionOutcome.ACCEPTED
    assert set(outcomes[1:]) == {ConsumptionOutcome.DUPLICATE_EVENT}

    result = question_statistics(
        database, principal=teacher_principal(), question_id=_question.question_id, now=clock()
    )
    assert result.attempt_count == 1


def test_same_run_with_a_new_event_id_is_still_one_attempt(
    database, settings, published_version, clock, clean_event_store
):
    _question, version = published_version
    consumer = EvaluationCompletedConsumer(database, clock=clock)
    run_id = uuid.uuid4()
    first = event_envelope(
        question_version_id=version.question_version_id, evaluation_run_id=run_id, is_pass=True
    )
    second = event_envelope(
        question_version_id=version.question_version_id, evaluation_run_id=run_id, is_pass=True
    )
    assert consumer.handle(first).outcome is ConsumptionOutcome.ACCEPTED
    assert consumer.handle(second).outcome is ConsumptionOutcome.DUPLICATE_RUN
    assert consumer.stored_event_count() == 1


# --- TC-STT-02: replay stability ----------------------------------------


def test_rebuild_from_the_inbox_reproduces_incremental_values(
    database, settings, published_version, clock, clean_event_store
):
    _question, version = published_version
    consumer = EvaluationCompletedConsumer(database, clock=clock)
    moment = clock()
    envelopes = [
        event_envelope(
            question_version_id=version.question_version_id,
            is_pass=index % 2 == 0,
            score=100 - index * 10,
            failed_category="timeout" if index == 1 else None,
            occurred_at=(moment + timedelta(minutes=index)).isoformat().replace("+00:00", "Z"),
        )
        for index in range(6)
    ]
    consume_all(consumer, envelopes)

    before = question_statistics(
        database, principal=teacher_principal(), question_id=_question.question_id, now=clock()
    )
    summary = consumer.rebuild()
    assert summary["event_count"] == 6
    after = question_statistics(
        database, principal=teacher_principal(), question_id=_question.question_id, now=clock()
    )
    assert before.attempt_count == after.attempt_count
    assert before.pass_rate == after.pass_rate
    assert before.score_distribution == after.score_distribution
    assert before.failure_category_distribution == after.failure_category_distribution


def test_replay_out_of_order_produces_the_same_numbers(
    database, settings, published_version, clock, clean_event_store
):
    _question, version = published_version
    consumer = EvaluationCompletedConsumer(database, clock=clock)
    moment = clock()
    envelopes = [
        event_envelope(
            question_version_id=version.question_version_id,
            is_pass=index % 3 == 0,
            score=(index * 7) % 100,
            failed_category=["lint", "timeout", "wrong_output"][index % 3],
            occurred_at=(moment + timedelta(minutes=index)).isoformat().replace("+00:00", "Z"),
        )
        for index in range(9)
    ]
    consume_all(consumer, envelopes)
    forward = question_statistics(
        database, principal=teacher_principal(), question_id=_question.question_id, now=clock()
    )

    # Wipe the pre-aggregated tables and replay the same events in reverse order.
    with database.app_session() as session:
        from autograder_b3.infra.repositories import EventStoreRepository

        EventStoreRepository(session).clear_statistics()
    for envelope in reversed(envelopes):
        row = parse_evaluation_completed(envelope)
        with database.app_session() as session:
            from autograder_b3.stats.aggregator import apply_event_increment

            apply_event_increment(session, row, now=clock())

    backward = question_statistics(
        database, principal=teacher_principal(), question_id=_question.question_id, now=clock()
    )
    assert forward.attempt_count == backward.attempt_count
    assert forward.pass_rate == backward.pass_rate
    assert forward.score_distribution == backward.score_distribution
    assert forward.failure_category_distribution == backward.failure_category_distribution


def test_rebuild_after_clearing_the_statistics_tables(
    database, settings, published_version, clock, clean_event_store
):
    _question, version = published_version
    consumer = EvaluationCompletedConsumer(database, clock=clock)
    consume_all(
        consumer,
        [event_envelope(question_version_id=version.question_version_id, is_pass=True) for _ in range(3)],
    )
    with database.app_session() as session:
        report = rebuild_statistics(session, now=clock())
    assert report.event_count == 3
    assert sum(item.attempt_count for item in report.totals.values()) == 3


# --- windowed queries ----------------------------------------------------


def test_time_windowed_query_agrees_with_the_full_aggregate(
    database, settings, published_version, clock, clean_event_store
):
    _question, version = published_version
    consumer = EvaluationCompletedConsumer(database, clock=clock)
    base = datetime(2026, 9, 15, 10, 0, tzinfo=UTC)
    for index in range(4):
        consumer.handle(
            event_envelope(
                question_version_id=version.question_version_id,
                is_pass=True,
                score=50,
                occurred_at=(base + timedelta(hours=index)).isoformat().replace("+00:00", "Z"),
            )
        )
    full = question_statistics(
        database, principal=teacher_principal(), question_id=_question.question_id, now=clock()
    )
    windowed = question_statistics(
        database,
        principal=teacher_principal(),
        question_id=_question.question_id,
        from_at=base,
        to_at=base + timedelta(hours=1),
        now=clock(),
    )
    assert full.attempt_count == 4
    assert windowed.attempt_count == 2

    from autograder_b3.errors import StatisticsQueryInvalid

    with pytest.raises(StatisticsQueryInvalid):
        question_statistics(
            database,
            principal=teacher_principal(),
            question_id=_question.question_id,
            from_at=base + timedelta(hours=3),
            to_at=base,
            now=clock(),
        )


def test_grouping_is_per_question_version(database, settings, published_version, clock, clean_event_store):
    _question, version = published_version
    other_version = uuid.uuid4()
    consumer = EvaluationCompletedConsumer(database, clock=clock)
    consumer.handle(event_envelope(question_version_id=version.question_version_id, is_pass=True))
    consumer.handle(event_envelope(question_version_id=other_version, is_pass=False))

    per_version = question_statistics(
        database,
        principal=teacher_principal(),
        question_id=_question.question_id,
        question_version_id=version.question_version_id,
        now=clock(),
    )
    assert per_version.attempt_count == 1
    assert per_version.question_version_id == version.question_version_id


# --- TC-STT-04: payload whitelist ---------------------------------------


@pytest.mark.parametrize(
    "field",
    [
        "student_id",
        "source_code",
        "reference_answer",
        "expected_output",
        "expected_stdout",
        "hidden_cases",
        "student_name",
        "email",
        "token",
    ],
)
def test_forbidden_payload_fields_are_rejected_and_not_counted(
    database, settings, published_version, clock, clean_event_store, field
):
    _question, version = published_version
    consumer = EvaluationCompletedConsumer(database, clock=clock)
    envelope = event_envelope(
        question_version_id=version.question_version_id,
        extra_payload={field: "sensitive"},
    )
    result = consumer.handle(envelope)
    assert result.outcome is ConsumptionOutcome.REJECTED
    assert result.should_ack is False
    assert result.field == field
    assert "sensitive" not in (result.reason or "")
    assert consumer.stored_event_count() == 0


def test_unknown_payload_field_is_rejected(database, settings, published_version, clock, clean_event_store):
    _question, version = published_version
    consumer = EvaluationCompletedConsumer(database, clock=clock)
    result = consumer.handle(
        event_envelope(question_version_id=version.question_version_id, extra_payload={"new_thing": 1})
    )
    assert result.outcome is ConsumptionOutcome.REJECTED
    assert "whitelist" in (result.reason or "")


def test_whitelist_and_forbidden_sets_are_documented_and_disjoint():
    assert ALLOWED_PAYLOAD_FIELDS & FORBIDDEN_PAYLOAD_FIELDS == set()
    assert "evaluation_run_id" in ALLOWED_PAYLOAD_FIELDS
    assert "question_version_id" in ALLOWED_PAYLOAD_FIELDS
    assert "student_id" in FORBIDDEN_PAYLOAD_FIELDS


def test_envelope_from_another_producer_is_rejected(database, settings, published_version, clock):
    _question, version = published_version
    consumer = EvaluationCompletedConsumer(database, clock=clock)
    envelope = event_envelope(question_version_id=version.question_version_id)
    envelope["producer"] = "B4"
    result = consumer.handle(envelope)
    assert result.outcome is ConsumptionOutcome.REJECTED
    assert "B2" in (result.reason or "")


def test_malformed_envelope_is_rejected(clock, database):
    consumer = EvaluationCompletedConsumer(database, clock=clock)
    result = consumer.handle({"event_type": "autograder.v1.evaluation.completed"})
    assert result.outcome is ConsumptionOutcome.REJECTED
    assert result.reason


def test_event_without_a_utc_offset_is_rejected(database, settings, published_version, clock):
    _question, version = published_version
    consumer = EvaluationCompletedConsumer(database, clock=clock)
    envelope = event_envelope(question_version_id=version.question_version_id)
    envelope["occurred_at"] = "2026-09-15T12:00:00"
    assert consumer.handle(envelope).outcome is ConsumptionOutcome.REJECTED


def test_inbox_payload_round_trips_through_the_whitelist(
    database, settings, published_version, clock, clean_event_store
):
    _question, version = published_version
    consumer = EvaluationCompletedConsumer(database, clock=clock)
    envelope = event_envelope(
        question_version_id=version.question_version_id,
        is_pass=False,
        score=42,
        failed_category="compile_error",
    )
    consumer.handle(envelope)

    with database.app_session() as session:
        from autograder_b3.infra.models import EventInbox
        from autograder_b3.events.whitelist import event_from_inbox_row

        row = session.execute(select(EventInbox)).scalars().one()
        rebuilt = event_from_inbox_row(row)
    assert rebuilt.question_version_id == version.question_version_id
    assert rebuilt.is_pass is False
    assert rebuilt.score == 42.0
    assert rebuilt.failed_category == "compile_error"


def test_statistics_never_expose_identity_or_source(database, settings, published_version, clock):
    _question, version = published_version
    consumer = EvaluationCompletedConsumer(database, clock=clock)
    consumer.handle(event_envelope(question_version_id=version.question_version_id, is_pass=True))
    result = question_statistics(
        database, principal=teacher_principal(), question_id=_question.question_id, now=clock()
    )
    payload = result.as_dict()
    forbidden = {
        "student_id",
        "submission_id",
        "source_code",
        "score",
        "max_score",
        "evaluation_run_id",
        "result_digest",
    }
    assert set(payload) & forbidden == set()
    assert "student" not in str(payload).lower()


def test_rejected_event_leaves_no_row_behind(database, settings, published_version, clock):
    _question, version = published_version
    consumer = EvaluationCompletedConsumer(database, clock=clock)
    before = consumer.stored_event_count()
    result = consumer.handle(
        event_envelope(question_version_id=version.question_version_id, extra_payload={"student_id": "x"})
    )
    assert result.outcome is ConsumptionOutcome.REJECTED
    assert consumer.stored_event_count() == before


# --- consumption health: "nothing to consume" vs "everything rejected" ---


def test_health_is_idle_before_anything_is_delivered(database, clock, clean_event_store):
    """An empty database used to be the only observable state, and it is ambiguous."""
    from autograder_b3.stats.health import ConsumptionState, consumption_health

    health = consumption_health(database)
    assert health.state is ConsumptionState.IDLE
    assert health.healthy is True
    assert health.accepted == 0 and health.rejected == 0
    assert "no event has ever been delivered" in health.describe()


def test_health_reports_consuming_after_an_accepted_event(
    database, settings, published_version, clock, clean_event_store
):
    from autograder_b3.stats.health import ConsumptionState, consumption_health

    _question, version = published_version
    consumer = EvaluationCompletedConsumer(database, clock=clock)
    consumer.handle(event_envelope(question_version_id=version.question_version_id))

    health = consumption_health(database)
    assert health.state is ConsumptionState.CONSUMING
    assert health.accepted == 1
    assert health.inbox_rows == 1
    assert health.unprocessed_rows == 0


def test_health_counts_rejected_deliveries_that_never_reach_the_inbox(
    database, settings, published_version, clock, clean_event_store
):
    """A rejected event is not acked and is never written to the inbox.

    Without a counter for it, "the payload was refused" and "nothing was published"
    produced the same three empty tables - the failure mode this surface removes.
    """
    from autograder_b3.stats.health import ConsumptionState, consumption_health

    _question, version = published_version
    consumer = EvaluationCompletedConsumer(database, clock=clock)
    rejected = consumer.handle(
        event_envelope(
            question_version_id=version.question_version_id,
            extra_payload={"student_id": "s-1"},
        )
    )
    assert rejected.outcome is ConsumptionOutcome.REJECTED

    health = consumption_health(database)
    assert health.state is ConsumptionState.DEGRADED
    assert health.healthy is False
    assert health.rejected == 1
    assert health.inbox_rows == 0
    assert "degraded" in health.describe()
    assert "rejected" in health.describe()


def test_health_counts_duplicates_separately_from_rejections(
    database, settings, published_version, clock, clean_event_store
):
    from autograder_b3.stats.health import ConsumptionState, consumption_health

    _question, version = published_version
    consumer = EvaluationCompletedConsumer(database, clock=clock)
    envelope = event_envelope(question_version_id=version.question_version_id)
    consumer.handle(envelope)
    consumer.handle(envelope)

    health = consumption_health(database)
    assert health.state is ConsumptionState.CONSUMING
    assert health.accepted == 1
    assert health.duplicate_event == 1
    assert health.rejected == 0


def test_health_is_unknown_when_the_store_cannot_be_read():
    from autograder_b3.stats.health import ConsumptionState, consumption_health

    class BrokenDatabase:
        def app_session(self):
            raise RuntimeError("connection refused")

    health = consumption_health(BrokenDatabase())
    assert health.state is ConsumptionState.UNKNOWN
    assert health.healthy is False  # "unknown" is never reported as healthy


def test_plugin_health_detail_carries_the_consumption_state(dependencies, clean_event_store):
    from autograder_b3.api.app import health_detail

    detail = health_detail(dependencies)
    assert detail.startswith("question bank ready;")
    assert "statistics consumption: idle" in detail


# --- bounded window scans -------------------------------------------------


def test_the_window_scan_is_paged_and_bounded(database, settings, published_version, clock):
    """The window read must not materialise the whole window.

    ``inbox_rows_in_window`` used to be ``list(...)`` with no limit, so a wide window
    was a memory spike. This asserts the paging contract directly: each page is at most
    ``page_size`` rows, and the walk still yields every row of the window exactly once.
    """
    from autograder_b3.infra.repositories import EventStoreRepository

    _question, version = published_version
    consumer = EvaluationCompletedConsumer(database, clock=clock)
    for index in range(7):
        consumer.handle(
            event_envelope(
                question_version_id=version.question_version_id,
                occurred_at=f"2026-09-15T12:0{index}:00.000Z",
            )
        )

    with database.app_session() as session:
        store = EventStoreRepository(session)
        rows = list(
            store.inbox_rows_in_window(
                version_ids=[version.question_version_id], from_at=None, to_at=None, page_size=2
            )
        )
        assert len(rows) == 7
        assert len({row.event_id for row in rows}) == 7
        assert [row.occurred_at for row in rows] == sorted(row.occurred_at for row in rows)

        # A window that holds more events than the stated ceiling is refused, not
        # silently truncated into a partial aggregate.
        with pytest.raises(StatisticsWindowTooLarge):
            list(
                store.inbox_rows_in_window(
                    version_ids=[version.question_version_id],
                    from_at=None,
                    to_at=None,
                    page_size=2,
                    max_rows=3,
                )
            )


def test_window_too_large_is_a_stated_422(database, settings, published_version, clock):
    """The stated ceiling must surface as a refusal the caller can act on."""
    from autograder_b3.infra.repositories import EventStoreRepository

    _question, version = published_version
    consumer = EvaluationCompletedConsumer(database, clock=clock)
    for index in range(4):
        consumer.handle(
            event_envelope(
                question_version_id=version.question_version_id,
                occurred_at=f"2026-09-15T12:0{index}:00.000Z",
            )
        )

    with database.app_session() as session:
        with pytest.raises(StatisticsWindowTooLarge) as error:
            list(
                EventStoreRepository(session).inbox_rows_in_window(
                    version_ids=[version.question_version_id],
                    from_at=None,
                    to_at=None,
                    page_size=2,
                    max_rows=2,
                )
            )
    assert error.value.status == 422
    assert error.value.slug == "statistics-window-too-large"
    assert "narrow" in error.value.detail


def test_a_window_wider_than_one_page_aggregates_every_row(
    database, settings, published_version, clock, monkeypatch
):
    """Forces the multi-page path: the page size is smaller than the event count."""
    monkeypatch.setattr(
        "autograder_b3.infra.repositories.WINDOW_PAGE_SIZE", 2, raising=True
    )
    from autograder_b3.infra.repositories import EventStoreRepository

    _question, version = published_version
    consumer = EvaluationCompletedConsumer(database, clock=clock)
    for index in range(5):
        consumer.handle(
            event_envelope(
                question_version_id=version.question_version_id,
                score=50 + index,
                occurred_at=f"2026-09-15T12:0{index}:00.000Z",
            )
        )
    with database.app_session() as session:
        rows = list(
            EventStoreRepository(session).inbox_rows_in_window(
                version_ids=[version.question_version_id], from_at=None, to_at=None
            )
        )
    assert len(rows) == 5

    windowed = question_statistics(
        database,
        principal=teacher_principal(),
        question_id=_question.question_id,
        from_at=datetime(2026, 9, 1, tzinfo=UTC),
        to_at=datetime(2026, 10, 1, tzinfo=UTC),
        now=clock(),
    )
    assert windowed.attempt_count == 5


def test_result_counters_are_unchanged_by_paging(database, settings, published_version, clock):
    """Paging must be invisible in the numbers - it is a scan strategy, not a filter."""
    _question, version = published_version
    consumer = EvaluationCompletedConsumer(database, clock=clock)
    for index in range(5):
        consumer.handle(
            event_envelope(
                question_version_id=version.question_version_id,
                score=50 + index,
                occurred_at=f"2026-09-15T12:0{index}:00.000Z",
            )
        )
    windowed = question_statistics(
        database,
        principal=teacher_principal(),
        question_id=_question.question_id,
        from_at=datetime(2026, 9, 1, tzinfo=UTC),
        to_at=datetime(2026, 10, 1, tzinfo=UTC),
        now=clock(),
    )
    full = question_statistics(
        database, principal=teacher_principal(), question_id=_question.question_id, now=clock()
    )
    assert windowed.attempt_count == full.attempt_count == 5
    assert windowed.pass_rate == full.pass_rate
    assert windowed.score_distribution == full.score_distribution


def test_accumulator_is_order_independent():
    version_id = uuid.uuid4()
    first = Accumulator()
    second = Accumulator()
    events = [
        parse_evaluation_completed(
            event_envelope(
                question_version_id=version_id,
                is_pass=index % 2 == 0,
                score=index * 10,
                failed_category=["a", "b"][index % 2],
            )
        )
        for index in range(6)
    ]
    for event in events:
        first.add(event)
    for event in reversed(events):
        second.add(event)
    assert first.to_dict() == second.to_dict()
