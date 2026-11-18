"""Retention and storage-cap cleanup for ``logs.log_events``."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, delete, func, select, text
from sqlalchemy.engine import Engine

from .provision import resolve_logs_dsn
from .schema import (
    DEFAULT_RETENTION_INFO_DAYS,
    DEFAULT_RETENTION_WARN_ERROR_DAYS,
    DEFAULT_STORAGE_LIMIT_BYTES,
    log_events,
)

_logger = logging.getLogger("autograder_host.logs.retention")


@dataclass(frozen=True)
class CleanupReport:
    deleted_info: int
    deleted_warn_error: int
    deleted_capacity: int
    storage_bytes: int


def measure_storage_bytes(conn) -> int:
    """Measure ``logs.log_events`` storage bytes (PG relation size, fallback to sum(approx_bytes))."""
    if conn.dialect.name == "postgresql":
        try:
            size = conn.execute(
                text("SELECT pg_total_relation_size('logs.log_events')")
            ).scalar()
            if isinstance(size, int) and size >= 0:
                return size
        except Exception:
            pass
    approx = conn.execute(
        select(func.coalesce(func.sum(log_events.c.approx_bytes), 0))
    ).scalar()
    return int(approx or 0)


def run_retention_cleanup(
    ops_engine: Engine,
    *,
    info_days: int = DEFAULT_RETENTION_INFO_DAYS,
    warn_error_days: int = DEFAULT_RETENTION_WARN_ERROR_DAYS,
    max_bytes: int = DEFAULT_STORAGE_LIMIT_BYTES,
    now: datetime | None = None,
    batch_size: int = 500,
) -> CleanupReport:
    """Enforce time retention (info vs warn/error) and storage cap (info -> warn -> error)."""
    current = now or datetime.now(UTC)
    info_cutoff = current - timedelta(days=max(1, info_days))
    warn_err_cutoff = current - timedelta(days=max(1, warn_error_days))

    deleted_info = 0
    deleted_warn_error = 0
    deleted_capacity = 0

    with ops_engine.begin() as conn:
        res_info = conn.execute(
            delete(log_events).where(
                log_events.c.level == "info",
                log_events.c.occurred_at < info_cutoff,
            )
        )
        deleted_info = int(res_info.rowcount or 0)

        res_we = conn.execute(
            delete(log_events).where(
                log_events.c.level.in_(("warn", "error")),
                log_events.c.occurred_at < warn_err_cutoff,
            )
        )
        deleted_warn_error = int(res_we.rowcount or 0)

        # Enforce hard storage cap: delete oldest info first, then warn, then error.
        for target_level in ("info", "warn", "error"):
            while measure_storage_bytes(conn) > max_bytes:
                oldest_ids = [
                    row[0]
                    for row in conn.execute(
                        select(log_events.c.log_id)
                        .where(log_events.c.level == target_level)
                        .order_by(log_events.c.occurred_at.asc(), log_events.c.log_id.asc())
                        .limit(batch_size)
                    ).fetchall()
                ]
                if not oldest_ids:
                    break
                res_cap = conn.execute(
                    delete(log_events).where(log_events.c.log_id.in_(oldest_ids))
                )
                deleted_capacity += int(res_cap.rowcount or 0)

        final_bytes = measure_storage_bytes(conn)

    report = CleanupReport(
        deleted_info=deleted_info,
        deleted_warn_error=deleted_warn_error,
        deleted_capacity=deleted_capacity,
        storage_bytes=final_bytes,
    )
    _logger.info(
        "logs.retention.cleaned",
        extra={
            "event": "logs.retention.cleaned",
            "deleted_info": report.deleted_info,
            "deleted_warn_error": report.deleted_warn_error,
            "deleted_capacity": report.deleted_capacity,
            "storage_bytes": report.storage_bytes,
        },
    )
    return report


def main() -> int:
    dsn = resolve_logs_dsn("LOGS_OPS_DATABASE_URL") or os.environ.get("PLATFORM_DATABASE_URL", "").strip()
    if not dsn:
        raise SystemExit("LOGS_OPS_DATABASE_URL or PLATFORM_DATABASE_URL is required")
    info_days = int(os.environ.get("LOGS_RETENTION_INFO_DAYS", str(DEFAULT_RETENTION_INFO_DAYS)))
    warn_days = int(os.environ.get("LOGS_RETENTION_WARN_ERROR_DAYS", str(DEFAULT_RETENTION_WARN_ERROR_DAYS)))
    max_bytes = int(os.environ.get("LOGS_STORAGE_LIMIT_BYTES", str(DEFAULT_STORAGE_LIMIT_BYTES)))
    engine = create_engine(dsn, future=True)
    try:
        report = run_retention_cleanup(
            engine,
            info_days=info_days,
            warn_error_days=warn_days,
            max_bytes=max_bytes,
        )
    finally:
        engine.dispose()
    print(
        f"retention cleanup: info={report.deleted_info} warn_error={report.deleted_warn_error} "
        f"capacity={report.deleted_capacity} storage_bytes={report.storage_bytes}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
