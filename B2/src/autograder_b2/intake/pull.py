"""Pull the mail intake once (CLI), so something actually reads the mailbox on a schedule.

Why a separate entry point: `EmailIntakeService` is assembled into the runtime, but nothing
calls `pull()` -- the API does not read the mailbox and there is no background task. Without a
trigger, a fully configured mailbox still delivers nothing, and the health endpoint has no way
to notice.

Usage::

    python -m autograder_b2.intake.pull --once
    python -m autograder_b2.intake.pull           # loop, sleep between passes

Design notes:

* **Idempotent by checkpoint**: `b2.email_checkpoints` records what was processed, so running
  the CLI more often than needed is safe -- a second pass sees nothing new.
* **Exit codes**: `2` when the intake is not configured/available (so a systemd timer can stay
  `ConditionPathExists`-free and simply report), `0` on a completed pass. A failed pass raises:
  systemd records the unit failure instead of a silent "nothing to do".
* It never writes enrollments; it only stages submissions or quarantines, like the API path.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from ..logging import get_logger, log_event
from ..runtime import build_b2_runtime

DEFAULT_IDLE_SECONDS = 60.0

logger = get_logger("intake.pull")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="B2 mail intake puller.")
    parser.add_argument("--once", action="store_true", help="one pass, then exit")
    parser.add_argument(
        "--allow-unconfigured",
        action="store_true",
        help=(
            "exit 0 when the mailbox is not configured; used by the systemd timer so that "
            "'nothing to do' is not reported as a failed unit"
        ),
    )
    parser.add_argument(
        "--idle-exit",
        action="store_true",
        help="exit as soon as a pass finds nothing to do (handy for checks)",
    )
    parser.add_argument(
        "--idle-seconds",
        type=float,
        default=DEFAULT_IDLE_SECONDS,
        help="sleep between passes in loop mode",
    )
    arguments = parser.parse_args(argv)

    runtime = build_b2_runtime(os.environ)
    if runtime.email is None:
        # Not configured is a legitimate state (the platform reports `not-configured`); the
        # CLI says so on stderr and exits 2 so the timer's status is honest.
        print(f"mail intake unavailable: {runtime.email_unavailable_reason}", file=sys.stderr)
        # 定时器场景下"邮箱未配置"是正常状态；命令行的默认语义仍是 2（明确不可用）。
        return 0 if arguments.allow_unconfigured else 2

    while True:
        report = runtime.email.pull()
        log_event(
            logger,
            "intake.pull",
            processed=len(getattr(report, "processed", ()) or ()),
            quarantined=len(getattr(report, "quarantined", ()) or ()),
            skipped=len(getattr(report, "skipped", ()) or ()),
            failed=len(getattr(report, "failed", ()) or ()),
        )
        if arguments.once:
            return 0
        if arguments.idle_exit and not any(
            getattr(report, name, ())
            for name in ("processed", "quarantined", "skipped", "failed")
        ):
            return 0
        time.sleep(arguments.idle_seconds)


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
