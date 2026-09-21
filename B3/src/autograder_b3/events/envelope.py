"""Event envelope handling (``MAIN/contracts/schemas/event-envelope.schema.json``).

B3 publishes ``autograder.v1.question.version.published`` through an outbox and
consumes ``autograder.v1.evaluation.completed`` (ADR-0006). Both directions use
this envelope: the field set is the contract's, not restated in prose.

Delivery is at-least-once, so nothing here may assume "processed once"; that is
the inbox's job (``events.whitelist`` + ``stats.aggregator``).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, UTC
from typing import Any
from collections.abc import Mapping

from ..errors import EventRejected

PRODUCER = "B3"
CONSUMER_EVENT_TYPE = "autograder.v1.evaluation.completed"
QUESTION_VERSION_PUBLISHED = "autograder.v1.question.version.published"
SCHEMA_VERSION = 1

ENVELOPE_KEYS = (
    "event_id",
    "event_type",
    "schema_version",
    "occurred_at",
    "correlation_id",
    "causation_id",
    "producer",
    "payload",
)


def parse_rfc3339(value: object, *, field: str) -> datetime:
    """Parse an RFC 3339 timestamp; reject naive values (time must be explicit)."""
    if not isinstance(value, str) or not value:
        raise EventRejected(f"{field} must be an RFC 3339 timestamp", field=field)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EventRejected(f"{field} is not a valid RFC 3339 timestamp", field=field) from exc
    if parsed.tzinfo is None:
        raise EventRejected(f"{field} must carry a UTC offset", field=field)
    return parsed.astimezone(UTC)


def parse_uuid(value: object, *, field: str, required: bool = True) -> uuid.UUID | None:
    if value is None:
        if required:
            raise EventRejected(f"{field} is required", field=field)
        return None
    if not isinstance(value, str):
        raise EventRejected(f"{field} must be a UUID string", field=field)
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise EventRejected(f"{field} is not a UUID", field=field) from exc


def format_rfc3339_millis(moment: datetime) -> str:
    from ..logging import to_rfc3339_millis

    return to_rfc3339_millis(moment)


@dataclass(frozen=True)
class EventEnvelope:
    event_id: uuid.UUID
    event_type: str
    schema_version: int
    occurred_at: datetime
    correlation_id: uuid.UUID
    producer: str
    payload: Mapping[str, Any]
    causation_id: uuid.UUID | None = None

    def to_wire(self) -> dict[str, Any]:
        return {
            "event_id": str(self.event_id),
            "event_type": self.event_type,
            "schema_version": self.schema_version,
            "occurred_at": format_rfc3339_millis(self.occurred_at),
            "correlation_id": str(self.correlation_id),
            "causation_id": None if self.causation_id is None else str(self.causation_id),
            "producer": self.producer,
            "payload": dict(self.payload),
        }


def build_question_version_published(
    *,
    question_id: uuid.UUID,
    question_version_id: uuid.UUID,
    course_id: uuid.UUID,
    content_digest: str,
    published_at: datetime,
    occurred_at: datetime,
    correlation_id: uuid.UUID,
    causation_id: uuid.UUID | None = None,
) -> EventEnvelope:
    """Build the publish event.

    Payload intentionally carries identifiers, a digest and timestamps only
    (``MAIN/docs/api/events.md`` §3.6): no reference answer, no case content, no
    fixture body. ``test_event_payload_is_deidentified`` asserts the key set.
    """
    return EventEnvelope(
        event_id=uuid.uuid4(),
        event_type=QUESTION_VERSION_PUBLISHED,
        schema_version=SCHEMA_VERSION,
        occurred_at=occurred_at,
        correlation_id=correlation_id,
        causation_id=causation_id,
        producer=PRODUCER,
        payload={
            "question_id": str(question_id),
            "question_version_id": str(question_version_id),
            "course_id": str(course_id),
            "content_digest": content_digest,
            "published_at": format_rfc3339_millis(published_at),
        },
    )
