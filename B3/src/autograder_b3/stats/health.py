"""Consumption health: "nothing to consume" vs "events are being rejected".

The statistics chain had never consumed a single real event, so ``event_inbox`` and
``question_version_stats`` were both empty - and an empty database looks exactly the
same whether nothing was published yet, the consumer is not running, or every delivery
is being rejected. The rejection path made it worse: a rejected event is never written
to the inbox (it stays unacked so JetStream routes it to the DLQ), so it left no trace
in the database at all.

This module reads the per-outcome counters the consumer now maintains and turns them
into one of three states. It reports; it never raises, because a health surface that
fails closed on its own query is worse than one that says "unknown".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class ConsumptionState(StrEnum):
    #: Counters all zero and the inbox is empty: nothing has been delivered yet.
    IDLE = "idle"
    #: Deliveries are arriving and none of them were refused.
    CONSUMING = "consuming"
    #: At least one delivery was rejected, or the inbox holds rows that were never
    #: processed - the two ways this chain fails loudly instead of silently.
    DEGRADED = "degraded"
    #: The counters could not be read at all. Never reported as healthy.
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ConsumptionHealth:
    state: ConsumptionState
    accepted: int
    duplicate_event: int
    duplicate_run: int
    rejected: int
    inbox_rows: int
    unprocessed_rows: int
    last_occurred_at: datetime | None

    @property
    def healthy(self) -> bool:
        """Only ``idle`` and ``consuming`` are healthy; ``unknown`` is not."""
        return self.state in (ConsumptionState.IDLE, ConsumptionState.CONSUMING)

    def as_dict(self) -> dict[str, object]:
        return {
            "state": str(self.state),
            "accepted": self.accepted,
            "duplicate_event": self.duplicate_event,
            "duplicate_run": self.duplicate_run,
            "rejected": self.rejected,
            "inbox_rows": self.inbox_rows,
            "unprocessed_rows": self.unprocessed_rows,
            "last_occurred_at": (
                None if self.last_occurred_at is None else self.last_occurred_at.isoformat()
            ),
            "healthy": self.healthy,
        }

    def describe(self) -> str:
        """One line for a health report or an operator's terminal."""
        if self.state is ConsumptionState.UNKNOWN:
            return "statistics consumption: unknown (counters unreadable)"
        if self.state is ConsumptionState.IDLE:
            return (
                "statistics consumption: idle, no event has ever been delivered "
                "(this is also what a missing consumer looks like)"
            )
        if self.state is ConsumptionState.DEGRADED:
            details = [f"{self.rejected} rejected"]
            if self.unprocessed_rows:
                details.append(f"{self.unprocessed_rows} unprocessed")
            return (
                "statistics consumption: degraded, "
                + " and ".join(details)
                + " - events are arriving but are not being counted"
            )
        duplicates = self.duplicate_event + self.duplicate_run
        return (
            f"statistics consumption: consuming, {self.accepted} accepted, "
            f"{duplicates} duplicate deliveries deduplicated"
        )


def consumption_health(database) -> ConsumptionHealth:
    """Read the consumption state. Never raises; an unreadable store is ``unknown``."""
    from ..infra.repositories import EventStoreRepository

    try:
        with database.app_session() as session:
            store = EventStoreRepository(session)
            counters = store.consumption_counters()
            inbox_rows = store.inbox_count()
            unprocessed = store.unprocessed_inbox_count()
            last_occurred_at = store.last_inbox_occurred_at()
            values = {
                outcome: (int(row.delivery_count), row.last_occurred_at)
                for outcome, row in counters.items()
            }
    except Exception:  # noqa: BLE001 - health must report, not propagate
        return ConsumptionHealth(
            state=ConsumptionState.UNKNOWN,
            accepted=0,
            duplicate_event=0,
            duplicate_run=0,
            rejected=0,
            inbox_rows=0,
            unprocessed_rows=0,
            last_occurred_at=None,
        )

    accepted = values.get("accepted", (0, None))[0]
    duplicate_event = values.get("duplicate_event", (0, None))[0]
    duplicate_run = values.get("duplicate_run", (0, None))[0]
    rejected = values.get("rejected", (0, None))[0]
    delivered = accepted + duplicate_event + duplicate_run + rejected

    if rejected or unprocessed:
        state = ConsumptionState.DEGRADED
    elif delivered == 0 and inbox_rows == 0:
        state = ConsumptionState.IDLE
    else:
        state = ConsumptionState.CONSUMING

    return ConsumptionHealth(
        state=state,
        accepted=accepted,
        duplicate_event=duplicate_event,
        duplicate_run=duplicate_run,
        rejected=rejected,
        inbox_rows=inbox_rows,
        unprocessed_rows=unprocessed,
        last_occurred_at=last_occurred_at,
    )
