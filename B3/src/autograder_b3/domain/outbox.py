"""Outbox drain (ADR-0006 §5).

Publish writes the event row inside the same transaction as the status change, so
the event cannot be lost. This module delivers pending rows and never deletes one:
a failed publish stays pending with its error recorded, which is what "不允许静默
丢弃" means in practice.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, UTC
from typing import Any

from ..infra.db import Database
from ..infra.publisher import EventPublisher
from ..logging import get_logger, log_event


@dataclass(frozen=True)
class DrainReport:
    attempted: int
    published: int
    failed: int

    def as_dict(self) -> dict[str, int]:
        return {
            "attempted": self.attempted,
            "published": self.published,
            "failed": self.failed,
        }


async def drain_outbox(
    database: Database,
    publisher: EventPublisher,
    *,
    clock=None,
    batch: int = 100,
) -> DrainReport:
    now = (clock or (lambda: datetime.now(UTC)))()
    attempted = 0
    published = 0
    failed = 0

    with database.app_session() as session:
        from ..infra.repositories import QuestionBankRepository

        bank = QuestionBankRepository(session)
        rows = bank.pending_outbox(batch)
        payloads: list[tuple[Any, str, dict[str, Any]]] = []
        for row in rows:
            payloads.append(
                (
                    row.outbox_id,
                    row.event_type,
                    {
                        "event_id": str(row.event_id),
                        "event_type": row.event_type,
                        "schema_version": row.schema_version,
                        "occurred_at": _iso(row.occurred_at),
                        "correlation_id": str(row.correlation_id),
                        "causation_id": None if row.causation_id is None else str(row.causation_id),
                        "producer": row.producer,
                        "payload": dict(row.payload),
                    },
                )
            )

        for outbox_id, subject, envelope in payloads:
            attempted += 1
            row = next(item for item in rows if item.outbox_id == outbox_id)
            try:
                await publisher.publish(subject, envelope)
            except Exception as error:  # noqa: BLE001 - recorded, never swallowed
                failed += 1
                bank.mark_event_failed(row, f"{type(error).__name__}: {error}")
                log_event(
                    get_logger("domain.outbox"),
                    "event.publish.failed",
                    level=40,
                    event_id=str(row.event_id),
                    reason=type(error).__name__,
                )
                continue
            published += 1
            bank.mark_event_published(row, now)
            log_event(
                get_logger("domain.outbox"),
                "event.published",
                event_id=str(row.event_id),
                outcome=subject,
            )
        return DrainReport(attempted=attempted, published=published, failed=failed)


def _iso(value: datetime) -> str:
    from ..logging import to_rfc3339_millis

    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return to_rfc3339_millis(value)
