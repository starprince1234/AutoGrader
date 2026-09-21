"""Events: envelope contract, outbox delivery, durable-consumer behaviour.

Covers the producer side (``question.version.published`` must satisfy
``MAIN/contracts/schemas/event-envelope.schema.json`` and carry no protected
content) and the consumer side (ack/leave-unacked decisions, malformed messages,
DLQ semantics). NATS itself is the only thing replaced - the message handling code
under test is the production one.
"""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest

from autograder_b3.domain.outbox import drain_outbox
from autograder_b3.dto.internal import PROTECTED_FIELD_NAMES
from autograder_b3.events.consumer import (
    ConsumptionOutcome,
    EvaluationCompletedConsumer,
    NatsEvaluationConsumer,
)
from autograder_b3.events.envelope import (
    CONSUMER_EVENT_TYPE,
    QUESTION_VERSION_PUBLISHED,
    EventEnvelope,
    build_question_version_published,
    parse_rfc3339,
)

from .conftest import COURSE_A, contract_json, draft_payload, event_envelope
from .support import create_published_question


def test_published_event_satisfies_the_envelope_contract(database, settings):
    import jsonschema

    question, version = create_published_question(database, settings, payload=draft_payload())
    with database.app_session() as session:
        from autograder_b3.infra.repositories import QuestionBankRepository

        rows = [
            row
            for row in QuestionBankRepository(session).pending_outbox(limit=500)
            if row.payload.get("question_version_id") == str(version.question_version_id)
        ]
    row = rows[0]
    envelope = {
        "event_id": str(row.event_id),
        "event_type": row.event_type,
        "schema_version": row.schema_version,
        "occurred_at": row.occurred_at.isoformat().replace("+00:00", "Z"),
        "correlation_id": str(row.correlation_id),
        "causation_id": None if row.causation_id is None else str(row.causation_id),
        "producer": row.producer,
        "payload": dict(row.payload),
    }
    jsonschema.validate(envelope, contract_json("schemas/event-envelope.schema.json"))
    assert envelope["event_type"] == QUESTION_VERSION_PUBLISHED
    assert envelope["producer"] == "B3"


def test_published_event_carries_no_protected_content(database, settings):
    question, version = create_published_question(database, settings, payload=draft_payload())
    with database.app_session() as session:
        from autograder_b3.infra.repositories import QuestionBankRepository

        rows = [
            row
            for row in QuestionBankRepository(session).pending_outbox(limit=500)
            if row.payload.get("question_version_id") == str(version.question_version_id)
        ]
    payload = rows[0].payload
    assert set(payload) & PROTECTED_FIELD_NAMES == set()
    rendered = json.dumps(payload)
    assert "print(3)" not in rendered  # reference answer body
    assert "3\\n" not in rendered  # expected stdout
    assert "counts lines" not in rendered  # case name


def test_envelope_builder_is_deidentified():
    envelope = build_question_version_published(
        question_id=uuid.uuid4(),
        question_version_id=uuid.uuid4(),
        course_id=COURSE_A,
        content_digest="a" * 64,
        published_at=parse_rfc3339("2026-09-15T08:00:00Z", field="t"),
        occurred_at=parse_rfc3339("2026-09-15T08:00:01Z", field="t"),
        correlation_id=uuid.uuid4(),
    )
    assert envelope.producer == "B3"
    wire = envelope.to_wire()
    assert wire["producer"] == "B3"
    assert wire["payload"]["content_digest"] == "a" * 64
    assert "reference_answer" not in wire["payload"]


def test_envelope_rejects_naive_timestamps():
    from autograder_b3.errors import EventRejected

    with pytest.raises(EventRejected):
        parse_rfc3339("2026-09-15T08:00:00", field="occurred_at")
    with pytest.raises(EventRejected):
        parse_rfc3339(12345, field="occurred_at")


def test_event_envelope_round_trips_through_serialization():
    envelope = EventEnvelope(
        event_id=uuid.uuid4(),
        event_type=QUESTION_VERSION_PUBLISHED,
        schema_version=1,
        occurred_at=parse_rfc3339("2026-09-15T08:00:00Z", field="t"),
        correlation_id=uuid.uuid4(),
        producer="B3",
        payload={"question_version_id": str(uuid.uuid4())},
    )
    wire = envelope.to_wire()
    assert json.loads(json.dumps(wire)) == wire
    assert wire["occurred_at"].endswith("Z")


# --- outbox drain ---------------------------------------------------------


def test_drain_outbox_publishes_pending_events(database, settings, publisher):
    question, version = create_published_question(database, settings, payload=draft_payload())
    report = asyncio.run(drain_outbox(database, publisher))
    assert report.attempted >= 1
    assert report.published == report.attempted
    assert report.failed == 0
    assert QUESTION_VERSION_PUBLISHED in publisher.subjects()

    second = asyncio.run(drain_outbox(database, publisher))
    assert second.attempted == 0


def test_failed_publish_is_recorded_and_retried(database, settings):
    class BrokenPublisher:
        def __init__(self) -> None:
            self.calls = 0

        async def publish(self, subject, envelope) -> None:
            self.calls += 1
            raise RuntimeError("nats is down")

        async def close(self) -> None:
            return None

    question, version = create_published_question(database, settings, payload=draft_payload())
    broken = BrokenPublisher()
    report = asyncio.run(drain_outbox(database, broken))
    assert report.failed >= 1

    with database.app_session() as session:
        from autograder_b3.infra.repositories import QuestionBankRepository

        still_pending = [
            row
            for row in QuestionBankRepository(session).pending_outbox(limit=500)
            if row.payload.get("question_version_id") == str(version.question_version_id)
        ]
    assert still_pending
    assert still_pending[0].attempts >= 1
    assert "nats is down" in (still_pending[0].last_error or "")


# --- durable consumer -----------------------------------------------------


class FakeMessage:
    def __init__(self, payload: bytes) -> None:
        self.data = payload
        self.acked = False

    async def ack(self) -> None:
        self.acked = True


def make_nats_consumer(database, clock) -> NatsEvaluationConsumer:
    return NatsEvaluationConsumer(
        EvaluationCompletedConsumer(database, clock=clock),
        nats_url="nats://unused",
        durable_name="b3-test",
    )


def test_valid_message_is_acked(database, settings, clock, clean_event_store):
    _question, version = create_published_question(database, settings, payload=draft_payload())
    consumer = make_nats_consumer(database, clock)
    message = FakeMessage(json.dumps(event_envelope(question_version_id=version.question_version_id)).encode())
    result = asyncio.run(consumer.handle_message(message))
    assert result.outcome is ConsumptionOutcome.ACCEPTED
    assert message.acked is True


def test_duplicate_message_is_acked_without_double_counting(database, settings, clock, clean_event_store):
    _question, version = create_published_question(database, settings, payload=draft_payload())
    consumer = make_nats_consumer(database, clock)
    payload = json.dumps(event_envelope(question_version_id=version.question_version_id)).encode()
    first = FakeMessage(payload)
    second = FakeMessage(payload)
    asyncio.run(consumer.handle_message(first))
    result = asyncio.run(consumer.handle_message(second))
    assert result.outcome is ConsumptionOutcome.DUPLICATE_EVENT
    assert second.acked is True


def test_private_payload_is_not_acked_so_it_can_reach_the_dlq(database, settings, clock):
    _question, version = create_published_question(database, settings, payload=draft_payload())
    consumer = make_nats_consumer(database, clock)
    envelope = event_envelope(
        question_version_id=version.question_version_id, extra_payload={"student_id": "leak"}
    )
    message = FakeMessage(json.dumps(envelope).encode())
    result = asyncio.run(consumer.handle_message(message))
    assert result.outcome is ConsumptionOutcome.REJECTED
    assert message.acked is False


def test_malformed_message_is_not_acked(database, clock):
    consumer = make_nats_consumer(database, clock)
    message = FakeMessage(b"not json")
    result = asyncio.run(consumer.handle_message(message))
    assert result.outcome is ConsumptionOutcome.REJECTED
    assert message.acked is False


def test_consumer_subject_is_the_contracted_one():
    assert EvaluationCompletedConsumer.subject == CONSUMER_EVENT_TYPE
    assert CONSUMER_EVENT_TYPE == "autograder.v1.evaluation.completed"


def test_event_from_inbox_row_revalidates_the_whitelist(database, settings, clock, clean_event_store):
    _question, version = create_published_question(database, settings, payload=draft_payload())
    consumer = EvaluationCompletedConsumer(database, clock=clock)
    consumer.handle(event_envelope(question_version_id=version.question_version_id, is_pass=True))

    with database.protected_session() as session:
        from sqlalchemy import text

        # A row that somehow acquired a forbidden field must not re-enter the
        # aggregation: replay re-validates instead of trusting stored data.
        session.execute(
            text(
                "UPDATE b3.event_inbox SET payload = payload || '{\"student_id\": \"x\"}'::jsonb "
                "WHERE question_version_id = :vid"
            ),
            {"vid": version.question_version_id},
        )
    from autograder_b3.errors import EventRejected
    from autograder_b3.events.whitelist import event_from_inbox_row
    from autograder_b3.infra.repositories import EventStoreRepository

    with database.app_session() as session:
        rows = [
            row
            for row in EventStoreRepository(session).inbox_rows_in_order()
            if row.question_version_id == version.question_version_id
        ]
    assert rows
    with pytest.raises(EventRejected):
        event_from_inbox_row(rows[0])
