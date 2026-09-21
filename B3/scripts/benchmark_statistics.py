"""Order-of-magnitude measurement for the windowed statistics query (``RQ-B-3-30``).

``RQ-B-3-30`` acceptance 5 claims the statistics chain stays usable at the 100k-event
order of magnitude with pagination and pre-aggregation. B3 recorded that acceptance as
done while neither half had been implemented or measured: the pre-aggregated totals did
not exist for the windowed path, and ``inbox_rows_in_window`` loaded an entire window
with ``list(...)`` and no ``limit``. This script produces the measurement the acceptance
criterion asks for, on the module-local PostgreSQL, at the required order of magnitude.

What it measures, per repetition:

* ``preaggregated`` - the no-window read path (``question_version_stats`` and its two
  bucket tables), which is what a dashboard hits;
* ``windowed_bounded`` - the windowed path with the production page size, walking the
  window in keyset pages;
* ``windowed_one_statement`` - the same window read as **one unbounded statement**, i.e.
  the shape the code had before this change, included as the baseline the bounded path is
  compared against. It is not a second implementation kept in the codebase; it is this
  script issuing the old query directly.

Peak memory is measured with :mod:`tracemalloc`, which counts Python allocations - the
ORM objects the unbounded read materialised. It deliberately excludes the
server-side buffers, so the numbers are a lower bound on the real difference, never an
exaggeration of it.

Usage::

    cd B3
    uv run python scripts/benchmark_statistics.py --events 100000 --versions 100

The script writes to the ``b3`` schema of the database ``DATABASE_URL`` points at and
truncates the event and statistics tables first. Run it against the module-local
database, never the platform one.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
import tracemalloc
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, delete, select, text
from sqlalchemy.orm import Session, sessionmaker

from autograder_b3.events.whitelist import event_from_inbox_row
from autograder_b3.infra.models import (
    ConsumptionCounter,
    EventInbox,
    QuestionVersionFailureCategory,
    QuestionVersionScoreBucket,
    QuestionVersionStats,
)
from autograder_b3.stats.aggregator import Accumulator, write_absolute_totals
from autograder_b3.stats.service import statistics_for_versions
from autograder_b3.infra.repositories import WINDOW_PAGE_SIZE

DEFAULT_URL = (
    "postgresql+psycopg://autograder_b3:change-me-local-only@127.0.0.1:55434/autograder"
)
SEED_BATCH = 5_000
WINDOW_DAYS = 30


def _events_per_version(total: int, versions: int) -> list[int]:
    base, remainder = divmod(total, versions)
    return [base + (1 if index < remainder else 0) for index in range(versions)]


def seed(session: Session, *, total_events: int, version_count: int) -> tuple[list[uuid.UUID], datetime, datetime]:
    """Insert ``total_events`` de-identified inbox rows spread over 30 days."""
    session.execute(delete(QuestionVersionFailureCategory))
    session.execute(delete(QuestionVersionScoreBucket))
    session.execute(delete(QuestionVersionStats))
    session.execute(delete(EventInbox))
    # Seed rows are inserted directly, bypassing the consumer, so the consumption
    # counters must be cleared too - otherwise the health surface would read 100k
    # deliveries that never happened.
    session.execute(delete(ConsumptionCounter))
    session.commit()

    version_ids = [uuid.uuid4() for _ in range(version_count)]
    occurred_start = datetime(2026, 8, 1, tzinfo=UTC)
    span_seconds = timedelta(days=WINDOW_DAYS).total_seconds()
    per_version = _events_per_version(total_events, version_count)

    index = 0
    batch: list[dict] = []
    for version_index, count in enumerate(per_version):
        for offset in range(count):
            index += 1
            occurred_at = occurred_start + timedelta(
                seconds=(index / total_events) * span_seconds
            )
            is_pass = offset % 3 != 0
            batch.append(
                {
                    "event_id": uuid.uuid4(),
                    "event_type": "autograder.v1.evaluation.completed",
                    "schema_version": 1,
                    "producer": "B2",
                    "occurred_at": occurred_at,
                    "correlation_id": uuid.uuid4(),
                    "causation_id": None,
                    "evaluation_run_id": uuid.uuid4(),
                    "question_version_id": version_ids[version_index],
                    "payload": {
                        "evaluation_run_id": str(uuid.uuid4()),
                        "question_version_id": str(version_ids[version_index]),
                        "is_pass": is_pass,
                        "score": float((offset * 7) % 101),
                        "max_score": 100.0,
                    },
                    "payload_digest": "0" * 64,
                    "received_at": occurred_at,
                    "processed_at": occurred_at,
                }
            )
            if len(batch) >= SEED_BATCH:
                session.execute(EventInbox.__table__.insert(), batch)
                session.commit()
                batch = []
    if batch:
        session.execute(EventInbox.__table__.insert(), batch)
        session.commit()

    return version_ids, occurred_start, occurred_start + timedelta(days=WINDOW_DAYS)


def _fill_preaggregated(session: Session, *, version_ids: list[uuid.UUID], now: datetime) -> None:
    """Populate the pre-aggregated tables the way the consumer would."""
    totals: dict[uuid.UUID, Accumulator] = {}
    for version_id in version_ids:
        accumulator = Accumulator()
        rows = session.execute(
            select(EventInbox).where(EventInbox.question_version_id == version_id)
        ).scalars()
        for inbox_row in rows:
            accumulator.add(event_from_inbox_row(inbox_row))
        totals[version_id] = accumulator
    write_absolute_totals(session, totals, now=now)
    session.commit()


def measure(callable_) -> tuple[float, int, object]:
    """Return ``(seconds, peak_bytes, result)`` for one call."""
    tracemalloc.start()
    started = time.perf_counter()
    result = callable_()
    elapsed = time.perf_counter() - started
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return elapsed, peak, result


def _unbounded_window(
    session: Session, *, version_ids: list[uuid.UUID], from_at: datetime, to_at: datetime
) -> int:
    """The pre-change query shape: every row of the window in one statement."""
    statement = (
        select(EventInbox)
        .where(EventInbox.question_version_id.in_(version_ids))
        .where(EventInbox.occurred_at >= from_at)
        .where(EventInbox.occurred_at <= to_at)
        .order_by(EventInbox.occurred_at, EventInbox.event_id)
    )
    return len(list(session.execute(statement).scalars()))


def main() -> int:
    parser = argparse.ArgumentParser(prog="benchmark_statistics")
    parser.add_argument("--events", type=int, default=100_000)
    parser.add_argument("--versions", type=int, default=100)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--url", default=os.environ.get("DATABASE_URL", DEFAULT_URL))
    arguments = parser.parse_args()

    engine = create_engine(arguments.url, future=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)

    with factory() as session:
        print(f"seeding {arguments.events} events across {arguments.versions} versions...")
        seed_started = time.perf_counter()
        version_ids, from_at, to_at = seed(
            session, total_events=arguments.events, version_count=arguments.versions
        )
        seed_seconds = time.perf_counter() - seed_started
        _fill_preaggregated(session, version_ids=version_ids, now=datetime.now(UTC))
        total_rows = session.execute(
            text("SELECT count(*) FROM b3.event_inbox")
        ).scalar_one()

    print(f"seeded in {seed_seconds:.1f}s; b3.event_inbox rows = {total_rows}")

    results: dict[str, object] = {
        "events": arguments.events,
        "versions": arguments.versions,
        "window_days": WINDOW_DAYS,
        "window_page_size": WINDOW_PAGE_SIZE,
        "event_inbox_rows": int(total_rows),
        "seed_seconds": round(seed_seconds, 2),
        "methods": {},
    }

    preaggregated: list[tuple[float, int]] = []
    windowed: list[tuple[float, int]] = []
    unbounded: list[tuple[float, int]] = []

    for _ in range(arguments.repeats):
        with factory() as session:
            elapsed, peak, result = measure(
                lambda: statistics_for_versions(session, version_ids=version_ids)
            )
            preaggregated.append((elapsed, peak))
            assert result.attempt_count == arguments.events, result.attempt_count

        with factory() as session:
            elapsed, peak, result = measure(
                lambda: statistics_for_versions(
                    session, version_ids=version_ids, from_at=from_at, to_at=to_at
                )
            )
            windowed.append((elapsed, peak))
            assert result.attempt_count == arguments.events, result.attempt_count

        with factory() as session:
            elapsed, peak, count = measure(
                lambda: _unbounded_window(
                    session, version_ids=version_ids, from_at=from_at, to_at=to_at
                )
            )
            unbounded.append((elapsed, peak))
            assert count == arguments.events, count

    def summarize(name: str, samples: list[tuple[float, int]], note: str) -> None:
        seconds = [item[0] for item in samples]
        peaks = [item[1] for item in samples]
        results["methods"][name] = {
            "median_seconds": round(statistics.median(seconds), 3),
            "min_seconds": round(min(seconds), 3),
            "max_seconds": round(max(seconds), 3),
            "median_peak_bytes": int(statistics.median(peaks)),
            "max_peak_bytes": max(peaks),
            "repeats": len(samples),
            "note": note,
        }
        print(
            f"{name:24s} median {statistics.median(seconds):7.3f}s "
            f"peak {statistics.median(peaks) / 1024 / 1024:7.2f} MiB"
        )

    summarize(
        "preaggregated",
        preaggregated,
        "no time window: reads b3.question_version_stats and its bucket tables",
    )
    summarize(
        "windowed_bounded",
        windowed,
        f"from/to window recomputed from event_inbox in {WINDOW_PAGE_SIZE}-row keyset pages",
    )
    summarize(
        "windowed_one_statement",
        unbounded,
        "the same window in ONE unbounded statement (the shape before the fix), "
        "measured as the baseline; not present in the codebase",
    )

    ratio = (
        results["methods"]["windowed_one_statement"]["median_peak_bytes"]
        / max(results["methods"]["windowed_bounded"]["median_peak_bytes"], 1)
    )
    results["peak_ratio_one_statement_over_bounded"] = round(ratio, 2)
    print(f"\npeak memory ratio (one statement / bounded pages) = {ratio:.1f}x")
    print(json.dumps(results, indent=2, sort_keys=True))

    engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
