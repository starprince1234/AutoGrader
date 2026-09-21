"""Event envelope construction.

The envelope schema is `MAIN/contracts/schemas/event-envelope.schema.json`; this module
builds values that satisfy it rather than restating it. The payload is deliberately
narrow: identifiers and counts only, never a name, number or e-mail, because the
AsyncAPI document forbids PII in every payload and the roster event is the one B4 emits.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

PRODUCER = "B4"
SCHEMA_VERSION = 1

ROSTER_IMPORT_COMPLETED = "autograder.v1.roster.import.completed"

#: Subjects B4 publishes as domain events. Exactly the one `module.yaml` declares.
PUBLISHED_EVENT_TYPES = (ROSTER_IMPORT_COMPLETED,)


@dataclass(frozen=True)
class EventEnvelope:
    """A validated envelope ready to be stored in the outbox and published."""

    event_id: uuid.UUID
    event_type: str
    schema_version: int
    occurred_at: datetime
    correlation_id: uuid.UUID
    causation_id: uuid.UUID | None
    producer: str
    payload: dict[str, Any]

    def as_document(self) -> dict[str, Any]:
        return {
            "event_id": str(self.event_id),
            "event_type": self.event_type,
            "schema_version": self.schema_version,
            "occurred_at": _rfc3339(self.occurred_at),
            "correlation_id": str(self.correlation_id),
            "causation_id": str(self.causation_id) if self.causation_id else None,
            "producer": self.producer,
            "payload": self.payload,
        }


def _rfc3339(moment: datetime) -> str:
    """UTC RFC 3339 with millisecond precision, the format the contracts declare."""
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def roster_import_completed_envelope(
    *,
    roster_import_id: uuid.UUID,
    course_id: uuid.UUID,
    section_id: uuid.UUID,
    accepted_rows: int,
    rejected_rows: int,
    correlation_id: uuid.UUID,
    occurred_at: datetime | None = None,
    causation_id: uuid.UUID | None = None,
) -> EventEnvelope:
    """Build the committed-roster event: counts and identifiers only, no PII."""
    if accepted_rows < 0 or rejected_rows < 0:
        raise ValueError("roster import counts must not be negative")
    return EventEnvelope(
        event_id=uuid.uuid4(),
        event_type=ROSTER_IMPORT_COMPLETED,
        schema_version=SCHEMA_VERSION,
        occurred_at=occurred_at or datetime.now(UTC),
        correlation_id=correlation_id,
        causation_id=causation_id,
        producer=PRODUCER,
        payload={
            "roster_import_id": str(roster_import_id),
            "course_id": str(course_id),
            "section_id": str(section_id),
            "accepted_rows": accepted_rows,
            "rejected_rows": rejected_rows,
        },
    )
