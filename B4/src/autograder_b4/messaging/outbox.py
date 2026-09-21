"""The transactional outbox.

The write path never talks to NATS. A business transaction inserts the event into
`b4.outbox_events` in the *same* transaction that changes the business rows, and a
separate relay publishes it afterwards. That is what makes "a committed change always
has its event" and "a NATS outage never blocks a synchronous API" true at the same time:
if the broker is down the row simply stays unpublished and is retried, and if the
transaction rolls back the event was never queued in the first place.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from nats.js import JetStreamContext
from sqlalchemy import Connection, Engine, select, update

from ..db.tables import outbox_events
from ..logging import get_logger
from .broker import safe_error
from .envelope import EventEnvelope

logger = get_logger("outbox")

DEFAULT_BATCH = 100


@dataclass(frozen=True)
class OutboxRecord:
    outbox_id: uuid.UUID
    event_id: uuid.UUID
    event_type: str
    payload: dict[str, object]


@dataclass(frozen=True)
class RelayResult:
    published: int
    failed: int
    remaining: int


def enqueue(connection: Connection, envelope: EventEnvelope) -> OutboxRecord:
    """Persist the event inside the caller's transaction.

    The caller's connection is used on purpose: this row and the business change commit
    together or not at all, so there is no window in which the change exists without its
    event.
    """
    outbox_id = uuid.uuid4()
    connection.execute(
        outbox_events.insert().values(
            outbox_id=outbox_id,
            event_id=envelope.event_id,
            event_type=envelope.event_type,
            schema_version=envelope.schema_version,
            occurred_at=envelope.occurred_at,
            correlation_id=envelope.correlation_id,
            causation_id=envelope.causation_id,
            producer=envelope.producer,
            payload=envelope.payload,
        )
    )
    logger.info(
        "outbox.enqueued",
        extra={
            "event": "outbox.enqueued",
            "event_id": str(envelope.event_id),
            "event_type": envelope.event_type,
        },
    )
    return OutboxRecord(
        outbox_id=outbox_id,
        event_id=envelope.event_id,
        event_type=envelope.event_type,
        payload=envelope.payload,
    )


def _document(row: object) -> dict[str, object]:
    return {
        "event_id": str(row.event_id),
        "event_type": row.event_type,
        "schema_version": row.schema_version,
        "occurred_at": row.occurred_at.astimezone(UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
        "correlation_id": str(row.correlation_id),
        "causation_id": str(row.causation_id) if row.causation_id else None,
        "producer": row.producer,
        "payload": row.payload,
    }


def pending_count(engine: Engine) -> int:
    with engine.connect() as connection:
        rows = connection.execute(
            select(outbox_events.c.outbox_id).where(outbox_events.c.published_at.is_(None))
        ).fetchall()
    return len(rows)


async def publish_pending(
    engine: Engine,
    jetstream: JetStreamContext,
    *,
    batch: int = DEFAULT_BATCH,
) -> RelayResult:
    """Publish unpublished events, oldest first, and record failures per row.

    Each row is claimed and settled in its own short transaction so that one poisoned
    message cannot hold up the rest, and so a crash mid-batch re-delivers rather than
    loses. Publishing the same `event_id` twice is safe by contract: consumers
    deduplicate on it (inbox pattern).
    """
    published = 0
    failed = 0
    with engine.connect() as connection:
        rows = connection.execute(
            select(outbox_events)
            .where(outbox_events.c.published_at.is_(None))
            .order_by(outbox_events.c.created_at, outbox_events.c.outbox_id)
            .limit(batch)
        ).fetchall()
    for row in rows:
        document = _document(row)
        try:
            await jetstream.publish(
                row.event_type,
                json.dumps(document, separators=(",", ":")).encode("utf-8"),
            )
        except Exception as error:  # noqa: BLE001 - any broker failure keeps the row queued
            failed += 1
            with engine.begin() as connection:
                connection.execute(
                    update(outbox_events)
                    .where(outbox_events.c.outbox_id == row.outbox_id)
                    .values(attempts=outbox_events.c.attempts + 1, last_error=safe_error(error))
                )
            logger.warning(
                "outbox.publish_failed",
                extra={
                    "event": "outbox.publish_failed",
                    "event_id": str(row.event_id),
                    "reason": safe_error(error),
                },
            )
            continue
        published += 1
        with engine.begin() as connection:
            connection.execute(
                update(outbox_events)
                .where(outbox_events.c.outbox_id == row.outbox_id)
                .values(
                    published_at=datetime.now(UTC),
                    attempts=outbox_events.c.attempts + 1,
                    last_error=None,
                )
            )
        logger.info(
            "outbox.published",
            extra={
                "event": "outbox.published",
                "event_id": str(row.event_id),
                "subject": row.event_type,
            },
        )
    return RelayResult(published=published, failed=failed, remaining=pending_count(engine))
