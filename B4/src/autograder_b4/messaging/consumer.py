"""Durable consumption, inbox deduplication, dead-letter capture and replay.

Delivery is at-least-once, which means a consumer *will* see the same `event_id` twice.
The unique key on `b4.inbox_events (consumer, event_id)` is what turns that into a single
business effect: the second delivery loses the insert race and the handler is skipped.

When a message exhausts `max_deliver`, JetStream stops redelivering it and emits a
`MSG_TERMINATED` advisory. The advisory lands in the `B4_DLQ` stream, is recorded in
`b4.dlq_messages`, and can be replayed from there by re-publishing the retained original
message. Nothing is discarded without a queryable record.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from nats.js import JetStreamContext
from nats.js.errors import NotFoundError
from sqlalchemy import Connection, Engine, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..db.tables import dlq_messages, inbox_events
from ..logging import get_logger, redact_text
from .broker import safe_error
from .jetstream import (
    CONSUMER_ROSTER_IMPORT,
    DLQ_CONSUMER_SPEC,
    STREAM_ROSTER_IMPORT,
    SUBJECT_ROSTER_IMPORT_COMPLETED,
)

logger = get_logger("consumer")

DLQ_CONSUMER_NAME = DLQ_CONSUMER_SPEC.name

#: Called once per first delivery; the second delivery of the same event never reaches it.
BusinessEffect = Callable[[Connection, dict[str, object]], None]


@dataclass(frozen=True)
class ConsumeOutcome:
    processed: int
    duplicates: int
    failed: int
    empty: bool = False


@dataclass(frozen=True)
class DlqRecord:
    dlq_id: uuid.UUID
    original_stream: str
    original_sequence: int | None
    event_id: uuid.UUID | None
    reason: str
    delivery_count: int
    replayed_at: datetime | None


def _record_delivery(
    connection: Connection, *, consumer: str, envelope: dict[str, object], subject: str
) -> bool:
    """Insert the delivery into the inbox ledger; False means it was already seen.

    `RETURNING` is what makes this decidable. psycopg3 reports `rowcount == -1` for
    inserts under SQLAlchemy, so counting affected rows would classify every delivery as
    a duplicate -- and the handler would never run. A conflicting insert returns no row.
    """
    statement = (
        pg_insert(inbox_events)
        .values(
            inbox_id=uuid.uuid4(),
            consumer=consumer,
            event_id=uuid.UUID(str(envelope["event_id"])),
            subject=subject,
        )
        .on_conflict_do_nothing(index_elements=["consumer", "event_id"])
        .returning(inbox_events.c.inbox_id)
    )
    return connection.execute(statement).fetchone() is not None


def _apply(
    engine: Engine,
    *,
    consumer: str,
    envelope: dict[str, object],
    subject: str,
    handler: BusinessEffect | None,
) -> str:
    """Apply one delivery atomically with its inbox record. Returns 'applied' or 'duplicate'."""
    with engine.begin() as connection:
        first_delivery = _record_delivery(
            connection, consumer=consumer, envelope=envelope, subject=subject
        )
        if not first_delivery:
            return "duplicate"
        if handler is not None:
            handler(connection, envelope)
    return "applied"


async def consume_roster_events(
    engine: Engine,
    jetstream: JetStreamContext,
    *,
    handler: BusinessEffect | None = None,
    batch: int = 10,
    timeout: float = 2.0,
    consumer: str = CONSUMER_ROSTER_IMPORT,
) -> ConsumeOutcome:
    """Fetch one batch and apply it. A malformed message is left un-acked on purpose.

    Not acknowledging is the only correct response to a message we cannot apply: it stays
    pending, is redelivered, and eventually terminates into the DLQ, where an operator can
    see it. Acknowledging it would be the silent drop the requirements forbid.
    """
    subscription = await jetstream.pull_subscribe(
        subject=SUBJECT_ROSTER_IMPORT_COMPLETED,
        stream=STREAM_ROSTER_IMPORT,
        durable=consumer,
    )
    try:
        try:
            messages = await subscription.fetch(batch, timeout=timeout)
        except Exception as error:
            if type(error).__name__ in {"TimeoutError", "NotFoundError"}:
                return ConsumeOutcome(0, 0, 0, empty=True)
            raise
        processed = duplicates = failed = 0
        for message in messages:
            try:
                envelope = json.loads(message.data.decode("utf-8"))
                if not isinstance(envelope, dict) or "event_id" not in envelope:
                    raise ValueError("envelope is not an object with event_id")
            except (ValueError, UnicodeDecodeError) as error:
                failed += 1
                logger.error(
                    "consumer.malformed",
                    extra={"event": "consumer.malformed", "reason": safe_error(error)},
                )
                await message.nak()
                continue
            outcome = _apply(
                engine,
                consumer=consumer,
                envelope=envelope,
                subject=message.subject,
                handler=handler,
            )
            if outcome == "duplicate":
                duplicates += 1
            else:
                processed += 1
            await message.ack()
        return ConsumeOutcome(processed, duplicates, failed)
    finally:
        await subscription.unsubscribe()


async def record_dlq_advisories(
    engine: Engine,
    jetstream: JetStreamContext,
    *,
    batch: int = 10,
    timeout: float = 2.0,
) -> int:
    """Turn exhaustion advisories into queryable dead-letter rows.

    The subject distinguishes the two cases: `MAX_DELIVERIES` means the broker stopped
    redelivering after the configured limit, `MSG_TERMINATED` means the consumer rejected
    the message outright. Both mean "this message will never be delivered again", so both
    are recorded.
    """
    subscription = await jetstream.pull_subscribe(
        subject=DLQ_CONSUMER_SPEC.filter_subjects[0],
        stream=DLQ_CONSUMER_SPEC.stream,
        durable=DLQ_CONSUMER_NAME,
    )
    recorded = 0
    try:
        try:
            messages = await subscription.fetch(batch, timeout=timeout)
        except Exception as error:
            if type(error).__name__ in {"TimeoutError", "NotFoundError"}:
                return 0
            raise
        for message in messages:
            try:
                advisory = json.loads(message.data.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                await message.term()
                continue
            stream = str(advisory.get("stream") or STREAM_ROSTER_IMPORT)
            sequence = advisory.get("stream_seq")
            attempts = advisory.get("deliveries", "unknown")
            reason = (
                f"consumer terminated the message after {attempts} deliveries"
                if "MSG_TERMINATED" in message.subject
                else f"max_deliver exhausted after {attempts} attempts"
            )
            insert = (
                pg_insert(dlq_messages)
                .values(
                    dlq_id=uuid.uuid4(),
                    original_stream=stream,
                    original_consumer=str(advisory.get("consumer") or CONSUMER_ROSTER_IMPORT),
                    original_subject=str(
                        advisory.get("subject") or SUBJECT_ROSTER_IMPORT_COMPLETED
                    ),
                    original_sequence=int(sequence) if sequence is not None else None,
                    event_id=None,
                    reason=reason,
                    delivery_count=int(advisory.get("deliveries") or 0),
                    envelope=None,
                )
                .on_conflict_do_nothing(index_elements=["original_stream", "original_sequence"])
                .returning(dlq_messages.c.dlq_id)
            )
            with engine.begin() as connection:
                inserted = connection.execute(insert).fetchone() is not None
            if inserted:
                recorded += 1
                logger.warning(
                    "dlq.recorded",
                    extra={
                        "event": "dlq.recorded",
                        "stream": stream,
                        "sequence": sequence,
                        "consumer": advisory.get("consumer"),
                        "advisory_type": advisory.get("type"),
                    },
                )
            await message.ack()
        return recorded
    finally:
        await subscription.unsubscribe()


async def record_exhausted_delivery(
    engine: Engine,
    *,
    envelope: dict[str, object],
    reason: str,
    delivery_count: int,
    stream: str = STREAM_ROSTER_IMPORT,
    consumer: str = CONSUMER_ROSTER_IMPORT,
    sequence: int | None = None,
) -> uuid.UUID:
    """Store a dead letter together with its payload, so replay does not depend on the stream.

    The advisory path covers terminations the broker performs itself. This path covers the
    case where the consumer already knows the message is unusable: keeping the envelope
    here means replay still works after stream retention has discarded the original.
    """
    dlq_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            pg_insert(dlq_messages)
            .values(
                dlq_id=dlq_id,
                original_stream=stream,
                original_consumer=consumer,
                original_subject=str(envelope.get("event_type") or SUBJECT_ROSTER_IMPORT_COMPLETED),
                original_sequence=sequence,
                event_id=uuid.UUID(str(envelope["event_id"])) if envelope.get("event_id") else None,
                reason=redact_text(reason)[:500],
                delivery_count=delivery_count,
                envelope=envelope,
            )
            .on_conflict_do_nothing(index_elements=["original_stream", "original_sequence"])
        )
    logger.warning(
        "dlq.exhausted",
        extra={
            "event": "dlq.exhausted",
            "event_id": envelope.get("event_id"),
            "delivery_count": delivery_count,
        },
    )
    return dlq_id


def list_dlq(
    engine: Engine, *, limit: int = 100, include_replayed: bool = False
) -> list[DlqRecord]:
    """Query the dead-letter store, newest first."""
    statement = select(dlq_messages).order_by(dlq_messages.c.created_at.desc()).limit(limit)
    if not include_replayed:
        statement = statement.where(dlq_messages.c.replayed_at.is_(None))
    with engine.connect() as connection:
        rows = connection.execute(statement).fetchall()
    return [
        DlqRecord(
            dlq_id=row.dlq_id,
            original_stream=row.original_stream,
            original_sequence=row.original_sequence,
            event_id=row.event_id,
            reason=row.reason,
            delivery_count=row.delivery_count,
            replayed_at=row.replayed_at,
        )
        for row in rows
    ]


async def replay_dlq(engine: Engine, jetstream: JetStreamContext, dlq_id: uuid.UUID) -> str:
    """Re-publish a dead letter to its original subject.

    The stored envelope is preferred; when the row came from a broker advisory the
    original message is fetched from the stream by sequence. If neither is available the
    replay fails loudly rather than pretending to have retried.
    """
    with engine.connect() as connection:
        row = connection.execute(
            select(dlq_messages).where(dlq_messages.c.dlq_id == dlq_id)
        ).fetchone()
    if row is None:
        raise KeyError(f"no dead letter with id {dlq_id}")
    payload = row.envelope
    if payload is None:
        if row.original_sequence is None:
            raise ValueError(
                "dead letter has neither a stored envelope nor a stream sequence; cannot replay"
            )
        try:
            raw = await jetstream.get_msg(row.original_stream, seq=row.original_sequence)
        except NotFoundError as error:
            raise ValueError(
                f"original message {row.original_stream}#{row.original_sequence} is no longer "
                "retained; replay is impossible"
            ) from error
        body = raw.data
        subject = raw.subject
    else:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        subject = row.original_subject
    await jetstream.publish(subject, body)
    with engine.begin() as connection:
        connection.execute(
            update(dlq_messages)
            .where(dlq_messages.c.dlq_id == dlq_id)
            .values(
                replayed_at=datetime.now(UTC),
                replay_attempts=dlq_messages.c.replay_attempts + 1,
            )
        )
    logger.info(
        "dlq.replayed",
        extra={"event": "dlq.replayed", "dlq_id": str(dlq_id), "subject": subject},
    )
    return subject
