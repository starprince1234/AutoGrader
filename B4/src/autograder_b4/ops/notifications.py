"""Send pending assignment-publication notifications (one bounded pass).

Run by a systemd timer (`autograder-notifications.timer`), one pass per invocation:

    python -m autograder_b4.ops.notifications [--limit 50]

Why a separate process instead of sending inside the request that publishes an
assignment: SMTP is slow and can fail, and the publication itself must not depend on it.
The API records what must be sent (recipient rows with explicit skipped reasons); this
process records what actually happened. A failure is a `failed` row with a reason and a
bounded retry schedule -- never a silently dropped message and never a "sent" without an
SMTP accept.

Environment: the B4 module normally reads an unprefixed `DATABASE_URL` because the
composition host hands each module its own view. This CLI runs standalone, so it accepts
the deployment's prefixed `B4_DATABASE_URL` and maps it to that unprefixed name; SMTP
comes from the ordinary `SMTP_*` variables. Nothing is printed except one JSON line of
counts (no address, no name).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from ..config import ConfigurationError, Settings
from ..db.engine import create_engine_from_settings
from ..logging import configure_logging
from ..notifications import DEFAULT_SEND_LIMIT, send_pass
from ..security.mailer import send_assignment_published

EXIT_OK = 0
EXIT_INFRASTRUCTURE = 1
EXIT_CONFIGURATION = 2


def _settings_from_environment() -> Settings:
    source = dict(os.environ)
    dsn = (source.get("B4_DATABASE_URL") or source.get("DATABASE_URL") or "").strip()
    if not dsn:
        raise ConfigurationError("B4_DATABASE_URL (or DATABASE_URL) is required")
    source["DATABASE_URL"] = dsn
    return Settings.from_environment(source)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autograder_b4.ops.notifications")
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_SEND_LIMIT,
        help=f"maximum deliveries handled in this pass (default {DEFAULT_SEND_LIMIT})",
    )
    arguments = parser.parse_args(argv)
    if arguments.limit < 1:
        print(json.dumps({"error": "limit must be positive"}))
        return EXIT_CONFIGURATION

    try:
        settings = _settings_from_environment()
    except ConfigurationError as error:
        print(json.dumps({"error": type(error).__name__, "detail": str(error)}))
        return EXIT_CONFIGURATION

    configure_logging()
    try:
        engine = create_engine_from_settings(settings)
        report = send_pass(
            engine,
            settings=settings,
            send_email=send_assignment_published,
            limit=arguments.limit,
        )
    except Exception as error:  # noqa: BLE001 - the pass reports, it does not crash-loop
        print(json.dumps({"error": type(error).__name__}))
        return EXIT_INFRASTRUCTURE

    print(json.dumps(report.as_dict(), sort_keys=True))
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - process entry point
    sys.exit(main())
