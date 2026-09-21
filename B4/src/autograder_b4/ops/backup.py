"""Operator entry point for a logical backup of the `b4` schema, with a structured record.

    uv run python -m autograder_b4.ops.backup --container b4-postgres-1

`RQ-B-4-10` acceptance 3 requires a backup and restore runbook, and `RQ-B-4-99`
acceptance 4 requires the backup *result* to be recorded rather than transcribed into a
document by hand -- the runbook is not a log. This is that emission point, and it performs
the dump the runbook describes:

* `pg_dump` runs **inside the database container** (the runbook's own procedure, and the
  only place the binary is guaranteed to exist), connects over the container's local
  socket, so **no password ever reaches a command line** and nothing is assembled into a
  shell string;
* the target database comes from the admin DSN's own target rather than from a second
  variable that could disagree with it, exactly as `ops/provision.py` does;
* the archive is read back far enough to check it is a custom-format dump, because a
  zero-byte or HTML-error-page "backup" is how a restore drill fails months later.

Restore stays a runbook procedure: it is a destructive operation on a live instance and
belongs in a human's hands, not behind a convenience flag (`docs/runbooks/database-operations.md`).
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..logging import configure_logging, get_logger, log_operation

logger = get_logger("ops.backup")

DEFAULT_SCHEMA = "b4"
DEFAULT_BACKUP_DIRECTORY = Path("backups")

#: The first bytes of a `pg_dump -Fc` archive. Checked because the failure it catches --
#: an error message where the archive should be -- produces a file that looks like a
#: backup until someone tries to restore it.
CUSTOM_ARCHIVE_MAGIC = b"PGDMP"


class BackupError(RuntimeError):
    """Raised when no usable dump was produced."""


@dataclass(frozen=True)
class BackupResult:
    """Where the archive went and what the operator should be told about it."""

    destination: Path
    bytes_written: int
    duration_ms: int
    schema: str
    database: str


def _connection_target(admin_dsn: str) -> tuple[str, str]:
    """`(role, database)` from the admin DSN; never from a second variable."""
    from sqlalchemy.engine import make_url

    url = make_url(admin_dsn)
    if not url.database:
        raise BackupError("the admin DSN does not name a database")
    if not url.username:
        raise BackupError("the admin DSN does not name a role")
    return url.username, url.database


def create_backup(
    *,
    container: str,
    admin_dsn: str,
    destination: Path,
    schema: str = DEFAULT_SCHEMA,
) -> BackupResult:
    """Dump `schema` with `pg_dump -Fc` inside `container`, recording the outcome."""
    role, database = _connection_target(admin_dsn)
    destination = destination.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "docker",
        "exec",
        container,
        "pg_dump",
        "-U",
        role,
        "-d",
        database,
        "--schema",
        schema,
        "-Fc",
    ]
    started = time.monotonic()
    with destination.open("wb") as archive:
        completed = subprocess.run(
            command,
            stdout=archive,
            stderr=subprocess.PIPE,
            check=False,
        )
    duration_ms = int((time.monotonic() - started) * 1000)
    if completed.returncode != 0:
        destination.unlink(missing_ok=True)
        # `stderr` is a free-text field, so the formatter's redaction applies to it like any
        # other value; only the exit code and the last line are kept.
        reason = _last_line(completed.stderr)
        log_operation(
            logger,
            "ops.backup.failed",
            level=logging.ERROR,
            schema=schema,
            database=database,
            container=container,
            exit_code=completed.returncode,
            duration_ms=duration_ms,
            reason=reason,
        )
        raise BackupError(f"pg_dump exited {completed.returncode}: {reason}")

    size = destination.stat().st_size
    with destination.open("rb") as archive:
        magic = archive.read(len(CUSTOM_ARCHIVE_MAGIC))
    if magic != CUSTOM_ARCHIVE_MAGIC:
        destination.unlink(missing_ok=True)
        log_operation(
            logger,
            "ops.backup.failed",
            level=logging.ERROR,
            schema=schema,
            database=database,
            container=container,
            exit_code=completed.returncode,
            duration_ms=duration_ms,
            reason="the archive does not start with the pg_dump custom-format header",
        )
        raise BackupError(
            f"{destination.name} is not a custom-format archive; it was removed instead of "
            "being left behind as a file that looks like a backup"
        )
    result = BackupResult(
        destination=destination,
        bytes_written=size,
        duration_ms=duration_ms,
        schema=schema,
        database=database,
    )
    log_operation(
        logger,
        "ops.backup.completed",
        schema=schema,
        database=database,
        container=container,
        # The file name rather than the whole path: the record has to answer "which dump
        # was taken", and an operator's home directory is not part of that answer.
        dump=destination.name,
        bytes_written=size,
        duration_ms=duration_ms,
    )
    return result


def _last_line(output: bytes) -> str:
    text = output.decode("utf-8", errors="replace").strip()
    return text.splitlines()[-1][:300] if text else "no error output"


def main(argv: list[str] | None = None) -> int:
    from .dotenv import load_environment_file

    configure_logging()
    load_environment_file()
    parser = argparse.ArgumentParser(
        description="Back up the b4 schema and record the result (RQ-B-4-10 / RQ-B-4-99)."
    )
    parser.add_argument(
        "--container", required=True, help="database container running pg_dump, e.g. b4-postgres-1"
    )
    parser.add_argument(
        "--destination",
        default=None,
        help="archive path; defaults to backups/b4-<UTC timestamp>.dump",
    )
    parser.add_argument("--schema", default=DEFAULT_SCHEMA, help="schema to dump (only b4)")
    arguments = parser.parse_args(argv)

    if arguments.schema != DEFAULT_SCHEMA:
        print(f"refusing: B4 backs up only the {DEFAULT_SCHEMA} schema", file=sys.stderr)
        return 2
    admin_dsn = os.getenv("B4_ADMIN_DATABASE_URL", "")
    if not admin_dsn:
        print("refusing: no admin DSN (set B4_ADMIN_DATABASE_URL)", file=sys.stderr)
        return 2
    destination = (
        Path(arguments.destination)
        if arguments.destination
        else DEFAULT_BACKUP_DIRECTORY
        / f"b4-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.dump"
    )
    try:
        result = create_backup(
            container=arguments.container,
            admin_dsn=admin_dsn,
            destination=destination,
            schema=arguments.schema,
        )
    except BackupError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"wrote {result.destination} ({result.bytes_written} bytes) in {result.duration_ms} ms")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
