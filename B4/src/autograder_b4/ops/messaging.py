"""Operator entry point for the messaging topology and the dead-letter store.

    uv run python -m autograder_b4.ops.messaging provision
    uv run python -m autograder_b4.ops.messaging relay          # drain the outbox once
    uv run python -m autograder_b4.ops.messaging consume        # apply pending deliveries
    uv run python -m autograder_b4.ops.messaging dlq list
    uv run python -m autograder_b4.ops.messaging dlq replay <dlq_id>
    uv run python -m autograder_b4.ops.messaging health
    uv run python -m autograder_b4.ops.messaging metrics        # measured storage vs ledger

These run as a separate process from the composition host on purpose. The outbox relay
and the durable consumer are background work; embedding them in the request-serving
process would tie the availability of synchronous endpoints to the broker, which is the
coupling `RQ-B-4-20` forbids.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid

from sqlalchemy import Engine

from ..config import ConfigurationError, Settings
from ..db.engine import create_engine_from_settings
from ..logging import configure_logging, get_logger
from ..messaging.broker import broker_health, collect_stream_usage, connect, provision_topology
from ..messaging.consumer import (
    consume_roster_events,
    list_dlq,
    record_dlq_advisories,
    replay_dlq,
)
from ..messaging.jetstream import JETSTREAM_LEDGER_MAX_BYTES
from ..messaging.outbox import pending_count, publish_pending

logger = get_logger("ops.messaging")


def _settings() -> Settings:
    from .dotenv import load_environment_file

    load_environment_file()
    return Settings.from_environment()


def _engine(settings: Settings) -> Engine:
    return create_engine_from_settings(settings)


async def _provision(settings: Settings) -> int:
    results = await provision_topology(settings.nats_url)
    for name, outcome in sorted(results.items()):
        print(f"{name}: {outcome}")
    return 0


async def _relay(settings: Settings) -> int:
    engine = _engine(settings)
    client, jetstream = await connect(settings.nats_url, name="autograder-b4-relay")
    try:
        result = await publish_pending(engine, jetstream)
    finally:
        await client.close()
    print(
        f"published={result.published} failed={result.failed} still_pending={result.remaining}"
    )
    return 0 if result.failed == 0 else 1


async def _consume(settings: Settings, rounds: int) -> int:
    engine = _engine(settings)
    client, jetstream = await connect(settings.nats_url, name="autograder-b4-consumer")
    totals = {"processed": 0, "duplicates": 0, "failed": 0, "advisories": 0}
    try:
        for _ in range(rounds):
            outcome = await consume_roster_events(engine, jetstream)
            totals["processed"] += outcome.processed
            totals["duplicates"] += outcome.duplicates
            totals["failed"] += outcome.failed
            totals["advisories"] += await record_dlq_advisories(engine, jetstream)
            if outcome.empty:
                break
    finally:
        await client.close()
    print(json.dumps(totals))
    return 0


async def _dlq_replay(settings: Settings, dlq_id: uuid.UUID) -> int:
    engine = _engine(settings)
    client, jetstream = await connect(settings.nats_url, name="autograder-b4-replay")
    try:
        subject = await replay_dlq(engine, jetstream, dlq_id)
    finally:
        await client.close()
    print(f"replayed {dlq_id} to {subject}")
    return 0


async def _health(settings: Settings) -> int:
    engine = _engine(settings)
    report = await broker_health(settings.nats_url)
    print(f"nats: {'ok' if report.healthy else 'unavailable'} - {report.detail}")
    print(f"outbox pending: {pending_count(engine)}")
    return 0 if report.healthy else 1


async def _metrics(settings: Settings) -> int:
    """Print measured JetStream storage next to the ledger line (acceptance 5).

    The command is the measurement path the resource ledger needs: it reads what the
    broker stores rather than repeating the quota B4 declared, and it exits non-zero when a
    stream is missing or when the measured total is over the budget.
    """
    usages = await collect_stream_usage(settings.nats_url)
    measured = 0
    missing = 0
    for usage in usages:
        if not usage.exists:
            missing += 1
            print(f"{usage.name}: MISSING - {usage.detail}")
            continue
        measured += usage.stored_bytes
        share = usage.stored_bytes / JETSTREAM_LEDGER_MAX_BYTES * 100
        print(
            f"{usage.name}: stored={usage.stored_bytes} bytes messages={usage.messages} "
            f"quota={usage.max_bytes} ledger_share={share:.2f}%"
        )
        for consumer in usage.consumers:
            print(
                f"  consumer {consumer.stream}/{consumer.name}: pending={consumer.pending} "
                f"ack_pending={consumer.ack_pending} redelivered={consumer.redelivered}"
            )
    total_share = measured / JETSTREAM_LEDGER_MAX_BYTES * 100
    print(
        f"total stored={measured} bytes of the {JETSTREAM_LEDGER_MAX_BYTES} byte ledger line "
        f"({total_share:.2f}%)"
    )
    if missing:
        return 1
    if measured > JETSTREAM_LEDGER_MAX_BYTES:  # pragma: no cover - discard=old caps each stream
        print("error: measured storage exceeds the resource ledger", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(description="B4 messaging operations (RQ-B-4-20).")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("provision", help="create or reconcile streams and consumers")
    subparsers.add_parser("relay", help="publish the pending outbox once")
    consume_parser = subparsers.add_parser("consume", help="apply pending deliveries")
    consume_parser.add_argument("--rounds", type=int, default=3)
    subparsers.add_parser("health", help="report broker reachability and outbox backlog")
    subparsers.add_parser("metrics", help="measure JetStream storage against the resource ledger")
    dlq_parser = subparsers.add_parser("dlq", help="inspect or replay dead letters")
    dlq_parser.add_argument("action", choices=["list", "replay"])
    dlq_parser.add_argument("dlq_id", nargs="?")
    dlq_parser.add_argument("--limit", type=int, default=50)
    arguments = parser.parse_args(argv)

    try:
        settings = _settings()
    except ConfigurationError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    if arguments.command == "provision":
        return asyncio.run(_provision(settings))
    if arguments.command == "relay":
        return asyncio.run(_relay(settings))
    if arguments.command == "consume":
        return asyncio.run(_consume(settings, arguments.rounds))
    if arguments.command == "health":
        return asyncio.run(_health(settings))
    if arguments.command == "metrics":
        return asyncio.run(_metrics(settings))
    if arguments.command == "dlq":
        engine = _engine(settings)
        if arguments.action == "list":
            records = list_dlq(engine, limit=arguments.limit, include_replayed=True)
            for record in records:
                state = "replayed" if record.replayed_at else "pending"
                print(
                    f"{record.dlq_id} {state} stream={record.original_stream} "
                    f"seq={record.original_sequence} deliveries={record.delivery_count} "
                    f"reason={record.reason}"
                )
            if not records:
                print("no dead letters")
            return 0
        if not arguments.dlq_id:
            print("error: dlq replay requires a dlq_id", file=sys.stderr)
            return 2
        try:
            return asyncio.run(_dlq_replay(settings, uuid.UUID(arguments.dlq_id)))
        except (KeyError, ValueError) as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
    return 2  # pragma: no cover - argparse rejects unknown commands first


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
