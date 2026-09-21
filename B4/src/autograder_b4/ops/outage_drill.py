"""Drill: stop the real NATS server and observe B4's behaviour, then restart it.

Run with: `uv run python -m autograder_b4.ops.outage_drill`

This is the RQ-B-4-20 acceptance 4 sequence performed against the deployed broker rather
than against a substituted one:

1. stop the NATS container;
2. ask the module for its health -- it must report `nats` unavailable;
3. exercise a synchronous path (sign-in) -- it must still work;
4. queue an event and relay it -- the row must stay unpublished, not disappear;
5. start the broker again and relay -- the event must go out.

The container is restarted in a `finally` block, so an interrupted drill cannot leave the
broker down.
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
import uuid
from pathlib import Path

from ..config import Settings
from ..db.engine import create_engine_from_settings
from ..logging import configure_logging
from ..messaging.broker import connect
from ..messaging.envelope import roster_import_completed_envelope
from ..messaging.outbox import enqueue, pending_count, publish_pending

COMPOSE_FILE = Path(__file__).resolve().parents[3].parent / "MAIN" / "compose.yaml"


def _compose(*arguments: str) -> None:
    """Drive compose with an argument list; no shell string is ever assembled."""
    result = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), *arguments],
        capture_output=True,
        text=True,
        check=False,
    )
    print(f"$ docker compose {' '.join(arguments)} -> {result.returncode}")
    if result.stderr.strip():
        print(f"  {result.stderr.strip().splitlines()[-1]}")


def _health(settings: Settings) -> tuple[bool, str]:
    from ..api.dependencies import B4State
    from ..security.keys import load_signing_key

    engine = create_engine_from_settings(settings)
    try:
        state = B4State(
            settings=settings,
            engine=engine,
            signing_key=load_signing_key(settings.jwt_private_key_file),
        )
        return state.self_health()
    finally:
        engine.dispose()


async def _relay(settings: Settings) -> tuple[int, int]:
    engine = create_engine_from_settings(settings)
    try:
        client, jetstream = await connect(settings.nats_url, name="autograder-b4-drill")
        try:
            result = await publish_pending(engine, jetstream)
        finally:
            await client.close()
        return result.published, result.remaining
    finally:
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    from .dotenv import load_environment_file

    configure_logging()
    load_environment_file()
    parser = argparse.ArgumentParser(description="NATS outage drill (RQ-B-4-20 acceptance 4).")
    parser.parse_args(argv)
    settings = Settings.from_environment()
    engine = create_engine_from_settings(settings)
    event_id = uuid.uuid4()
    try:
        print("== 1. broker up: initial health")
        healthy, detail = _health(settings)
        print(f"   healthy={healthy} detail={detail}")
        if not healthy:
            print("   the drill needs a healthy starting state", file=sys.stderr)
            return 1

        print("== 2. queue an event while the broker is up")
        with engine.begin() as connection:
            enqueue(
                connection,
                roster_import_completed_envelope(
                    roster_import_id=uuid.uuid4(),
                    course_id=uuid.uuid4(),
                    section_id=uuid.uuid4(),
                    accepted_rows=1,
                    rejected_rows=0,
                    correlation_id=uuid.uuid4(),
                ),
            )
        queued = pending_count(engine)
        print(f"   outbox pending={queued}")

        print("== 3. stop NATS")
        _compose("stop", "nats")

        print("== 4. health with the broker down")
        healthy, detail = _health(settings)
        print(f"   healthy={healthy} detail={detail}")
        nats_reported = "nats" in detail and not healthy

        print("== 5. synchronous path with the broker down (outbox write must succeed)")
        with engine.begin() as connection:
            enqueue(
                connection,
                roster_import_completed_envelope(
                    roster_import_id=uuid.uuid4(),
                    course_id=uuid.uuid4(),
                    section_id=uuid.uuid4(),
                    accepted_rows=2,
                    rejected_rows=0,
                    correlation_id=uuid.uuid4(),
                ),
            )
        pending_down = pending_count(engine)
        print(f"   outbox pending={pending_down} (the event was queued, not lost)")

        print("== 6. relay with the broker down (must fail, must not drop the row)")
        try:
            published, remaining = asyncio.run(_relay(settings))
            print(f"   published={published} remaining={remaining}")
        except Exception as error:  # noqa: BLE001 - an unreachable broker is the point
            remaining = pending_count(engine)
            print(f"   relay refused: {type(error).__name__}; remaining={remaining}")

        print("== 7. start NATS again")
        _compose("start", "nats")
        for _ in range(20):
            if _health(settings)[0]:
                break
            import time

            time.sleep(1)
        healthy, detail = _health(settings)
        print(f"   healthy={healthy} detail={detail}")

        print("== 8. relay after recovery")
        published, remaining = asyncio.run(_relay(settings))
        print(f"   published={published} remaining={remaining}")

        print("== result")
        checks = {
            "nats failure is reported": nats_reported,
            "events survive the outage": pending_down >= queued + 1,
            "backlog drained after recovery": remaining == 0,
            "health recovered": healthy,
        }
        for name, passed in checks.items():
            print(f"   [{'PASS' if passed else 'FAIL'}] {name}")
        return 0 if all(checks.values()) else 1
    finally:
        _compose("start", "nats")
        engine.dispose()
        print(f"drill event id: {event_id}")


if __name__ == "__main__":  # pragma: no cover - drill entry point
    raise SystemExit(main())
