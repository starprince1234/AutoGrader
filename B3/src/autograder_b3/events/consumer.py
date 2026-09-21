"""Durable consumer for ``autograder.v1.evaluation.completed`` (``RQ-B-3-30``).

Flow for one delivery:

1. validate the envelope and the payload against the de-identified whitelist;
2. claim the event in ``b3.event_inbox`` (``event_id`` primary, ``evaluation_run_id``
   business key) - a duplicate delivery is a no-op;
3. add the accepted event's delta to the pre-aggregated tables;
4. mark the row processed and ack.

A rejected event is **not** acked: JetStream redelivers it and eventually routes it
to the DLQ, so a privacy violation is visible rather than silently dropped
(ADR-0006 §6). Failures are logged with the reason and the offending field name,
never with the field's value (``RQ-B-3-99`` acceptance 3).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, UTC
from enum import StrEnum
from typing import Any
from collections.abc import Callable, Mapping

from ..compile.canonical import digest_of
from ..errors import EventRejected
from ..infra.db import Database
from ..infra.repositories import EventStoreRepository
from ..logging import get_logger, log_event
from ..stats.health import ConsumptionHealth, consumption_health
from ..stats.service import apply_consumed_event, rebuild_statistics
from .envelope import CONSUMER_EVENT_TYPE
from .whitelist import event_from_inbox_row, event_payload_for_inbox, parse_evaluation_completed


class ConsumptionOutcome(StrEnum):
    ACCEPTED = "accepted"
    DUPLICATE_EVENT = "duplicate_event"
    DUPLICATE_RUN = "duplicate_run"
    REJECTED = "rejected"


@dataclass(frozen=True)
class ConsumptionResult:
    outcome: ConsumptionOutcome
    event_id: str | None = None
    question_version_id: str | None = None
    reason: str | None = None
    field: str | None = None

    @property
    def should_ack(self) -> bool:
        return self.outcome is not ConsumptionOutcome.REJECTED


class EvaluationCompletedConsumer:
    """Synchronous core of the consumer; the NATS runner drives it."""

    subject = CONSUMER_EVENT_TYPE

    def __init__(
        self,
        database: Database,
        *,
        clock: Callable[[], datetime] | None = None,
        logger: Any = None,
    ) -> None:
        self.database = database
        self.clock = clock or (lambda: datetime.now(UTC))
        self.logger = logger or get_logger("events.consumer")

    # --- message handling -------------------------------------------------

    def handle(self, envelope: Mapping[str, Any]) -> ConsumptionResult:
        try:
            event = parse_evaluation_completed(envelope)
        except EventRejected as exc:
            self._record(ConsumptionOutcome.REJECTED, occurred_at=None)
            log_event(
                self.logger,
                "evaluation.completed.rejected",
                level=40,
                reason=exc.reason,
                field=exc.field,
                event_id=_safe(envelope, "event_id"),
            )
            return ConsumptionResult(
                outcome=ConsumptionOutcome.REJECTED,
                event_id=_safe(envelope, "event_id"),
                reason=exc.reason,
                field=exc.field,
            )

        now = self.clock()
        payload = event_payload_for_inbox(event)
        with self.database.app_session() as session:
            store = EventStoreRepository(session)
            row, outcome = store.claim_event(
                {
                    "event_id": event.event_id,
                    "event_type": CONSUMER_EVENT_TYPE,
                    "schema_version": int(envelope.get("schema_version", 1)),
                    "producer": "B2",
                    "occurred_at": event.occurred_at,
                    "correlation_id": event.correlation_id,
                    "causation_id": _uuid_or_none(envelope.get("causation_id")),
                    "evaluation_run_id": event.evaluation_run_id,
                    "question_version_id": event.question_version_id,
                    "payload": payload,
                    "payload_digest": digest_of(payload),
                    "received_at": now,
                    "processed_at": None,
                }
            )
            if outcome != "accepted":
                # Bumped here, not above: a duplicate delivery never reached the
                # aggregate, so its counter answers "was this stream healthy", which is
                # the question the health surface exists to answer.
                store.record_consumption(outcome, occurred_at=event.occurred_at)
                log_event(
                    self.logger,
                    "evaluation.completed.duplicate",
                    reason=outcome,
                    event_id=str(event.event_id),
                    evaluation_run_id=str(event.evaluation_run_id),
                    question_version_id=str(event.question_version_id),
                )
                return ConsumptionResult(
                    outcome=(
                        ConsumptionOutcome.DUPLICATE_EVENT
                        if outcome == "duplicate_event"
                        else ConsumptionOutcome.DUPLICATE_RUN
                        if outcome == "duplicate_run"
                        else ConsumptionOutcome.DUPLICATE_EVENT
                    ),
                    event_id=str(event.event_id),
                    question_version_id=str(event.question_version_id),
                    reason=outcome,
                )
            apply_consumed_event(session, event, now=now)
            store.mark_processed(event.event_id, now)
            store.record_consumption(
                ConsumptionOutcome.ACCEPTED, occurred_at=event.occurred_at
            )

        log_event(
            self.logger,
            "evaluation.completed.accepted",
            event_id=str(event.event_id),
            evaluation_run_id=str(event.evaluation_run_id),
            question_version_id=str(event.question_version_id),
            course_id=str(event.course_id) if event.course_id else None,
            outcome="accepted",
        )
        return ConsumptionResult(
            outcome=ConsumptionOutcome.ACCEPTED,
            event_id=str(event.event_id),
            question_version_id=str(event.question_version_id),
        )

    # --- replay -----------------------------------------------------------

    def rebuild(self) -> dict[str, Any]:
        """Recompute all statistics from the durable inbox (replay)."""
        now = self.clock()
        with self.database.app_session() as session:
            report = rebuild_statistics(session, now=now)
        summary = report.as_dict()
        log_event(
            self.logger,
            "statistics.rebuilt",
            event_count=summary["event_count"],
            version_count=summary["version_count"],
            attempt_count=summary["attempt_count"],
        )
        return summary

    def stored_event_count(self) -> int:
        with self.database.app_session() as session:
            return EventStoreRepository(session).inbox_count()

    def health(self) -> ConsumptionHealth:
        """Consumption state for the health surface (see ``stats/health.py``)."""
        return consumption_health(self.database)

    def record_malformed(self) -> None:
        """Count a delivery that could not even be decoded as an envelope."""
        self._record(ConsumptionOutcome.REJECTED, occurred_at=None)

    def _record(self, outcome: ConsumptionOutcome, *, occurred_at: datetime | None) -> None:
        """Bump one outcome counter on a path that has no session of its own.

        A rejected delivery never reaches ``event_inbox``, so without this the rejection
        path exists only in logs and the database cannot tell "nothing delivered" from
        "everything refused".
        """
        with self.database.app_session() as session:
            EventStoreRepository(session).record_consumption(
                outcome, occurred_at=occurred_at
            )


class NatsEvaluationConsumer:
    """JetStream durable subscription driving :class:`EvaluationCompletedConsumer`."""

    def __init__(
        self,
        consumer: EvaluationCompletedConsumer,
        *,
        nats_url: str,
        durable_name: str,
    ) -> None:
        self.consumer = consumer
        self.nats_url = nats_url
        self.durable_name = durable_name
        self._connection: Any = None

    async def handle_message(self, message: Any) -> ConsumptionResult:
        """Decode, process and ack/leave-unacked one JetStream message."""
        try:
            envelope = json.loads(message.data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self.consumer.record_malformed()
            log_event(
                get_logger("events.consumer"),
                "evaluation.completed.malformed",
                level=40,
                reason=type(exc).__name__,
            )
            # Left unacked on purpose: a malformed message must reach the DLQ.
            return ConsumptionResult(
                outcome=ConsumptionOutcome.REJECTED, reason="malformed envelope"
            )
        result = self.consumer.handle(envelope)
        if result.should_ack:
            await message.ack()
        return result

    async def run(self) -> None:
        import nats

        self._connection = await nats.connect(self.nats_url)
        jetstream = self._connection.jetstream()
        await jetstream.subscribe(
            self.consumer.subject,
            durable=self.durable_name,
            manual_ack=True,
            cb=self.handle_message,
        )

    async def stop(self) -> None:
        if self._connection is not None:
            await self._connection.close()
            self._connection = None


def _safe(envelope: Mapping[str, Any], key: str) -> str | None:
    value = envelope.get(key) if isinstance(envelope, Mapping) else None
    return value if isinstance(value, str) else None


def _uuid_or_none(value: Any) -> Any:
    import uuid

    if value is None:
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError):
        return None


def payload_from_row(row: Any) -> Any:
    """Exposed for tests: rebuild the typed event from an inbox row."""
    return event_from_inbox_row(row)
