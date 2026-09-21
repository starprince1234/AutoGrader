"""Standalone runner for the B3 asynchronous work: statistics consumption.

The composition host serves B3's synchronous API; the durable consumer and the
outbox drain are long-running loops. B4/MAIN can run them in-process or start this
module, which keeps the choice of process layout out of the plugin contract.

Usage (see ``docs/runbooks/local-development.md``)::

    uv run python -m autograder_b3.consumer_cli            # consume + drain
    uv run python -m autograder_b3.consumer_cli --rebuild  # rebuild stats, then exit
    uv run python -m autograder_b3.consumer_cli --health   # consumption state as JSON
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging

from .config import Settings
from .domain.outbox import drain_outbox
from .events.consumer import EvaluationCompletedConsumer, NatsEvaluationConsumer
from .infra.db import Database
from .infra.publisher import InMemoryEventPublisher, NatsEventPublisher
from .logging import configure_logging, get_logger, log_event
from .stats.health import consumption_health

DRAIN_INTERVAL_SECONDS = 5.0


async def run_forever(settings: Settings) -> None:
    database = Database(settings)
    consumer = EvaluationCompletedConsumer(database)
    publisher = (
        NatsEventPublisher(settings.nats_url) if settings.nats_url else InMemoryEventPublisher()
    )
    durable = (
        NatsEvaluationConsumer(
            consumer, nats_url=settings.nats_url, durable_name=settings.consumer_name
        )
        if settings.nats_url
        else None
    )
    if durable is None:
        raise SystemExit(
            "NATS_URL is not configured; the statistics consumer needs JetStream (RQ-B-4-20)"
        )
    await durable.run()
    log_event(
        get_logger("consumer.cli"),
        "consumer.started",
        outcome=settings.consumer_name,
    )
    try:
        while True:
            report = await drain_outbox(database, publisher)
            if report.attempted:
                log_event(
                    get_logger("consumer.cli"), "outbox.drained", attempt_count=report.attempted
                )
            await asyncio.sleep(DRAIN_INTERVAL_SECONDS)
    finally:
        with contextlib.suppress(Exception):
            await durable.stop()
        with contextlib.suppress(Exception):
            await publisher.close()
        database.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autograder_b3.consumer_cli")
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="rebuild the statistics tables from the durable inbox, then exit",
    )
    parser.add_argument(
        "--health",
        action="store_true",
        help=(
            "print the consumption state as JSON and exit; exit code 1 means the "
            "chain is degraded (events arriving but not counted)"
        ),
    )
    arguments = parser.parse_args(argv)
    settings = Settings.from_env()
    configure_logging(settings.log_level)
    database = Database(settings)
    try:
        if arguments.health:
            state = consumption_health(database)
            print(json.dumps(state.as_dict(), sort_keys=True, ensure_ascii=False))
            log_event(
                get_logger("consumer.cli"),
                "consumption.health.reported",
                outcome=str(state.state),
                level=logging.WARNING if not state.healthy else logging.INFO,
            )
            return 0 if state.healthy else 1
        if arguments.rebuild:
            summary = EvaluationCompletedConsumer(database).rebuild()
            log_event(
                get_logger("consumer.cli"),
                "statistics.rebuilt",
                attempt_count=int(summary["attempt_count"]),
                event_count=int(summary["event_count"]),
            )
            return 0
        asyncio.run(run_forever(settings))
    except KeyboardInterrupt:  # pragma: no cover - interactive use
        return 130
    finally:
        database.dispose()
    return 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
