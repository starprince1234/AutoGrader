"""Query, trace, summary, streaming export, and client log ingestion services."""

from __future__ import annotations

import base64
import csv
import io
import json
import logging
import threading
import time
from collections import defaultdict, deque
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import String, and_, case, cast, func, or_, select
from sqlalchemy.engine import Engine

from .auth import LogPrincipal, OpsLogAuthError
from .context import normalize_uuid
from .redaction import rfc3339_millis, sanitize_text
from .retention import measure_storage_bytes
from .schema import (
    DEFAULT_RETENTION_INFO_DAYS,
    DEFAULT_RETENTION_WARN_ERROR_DAYS,
    DEFAULT_STORAGE_LIMIT_BYTES,
    log_events,
)
from .sink import get_active_sink, get_sink_stats

_audit_logger = logging.getLogger("autograder_host.logs.audit")

MAX_QUERY_WINDOW_DAYS = 30
DEFAULT_QUERY_WINDOW_HOURS = 24
MAX_TRACE_ROWS = 500
MAX_EXPORT_ROWS = 5000
PRODUCERS_ORDER: tuple[str, ...] = ("B1", "B2", "B3", "B4", "B5", "MAIN")


@dataclass(frozen=True)
class LogFilter:
    from_dt: datetime
    to_dt: datetime
    producer: str | None = None
    level: str | None = None
    event: str | None = None
    request_id: str | None = None
    correlation_id: str | None = None
    submission_id: str | None = None
    course_id: str | None = None
    io_type: str | None = None
    q: str | None = None


def resolve_time_bounds(from_dt: datetime | None, to_dt: datetime | None) -> tuple[datetime, datetime]:
    now = datetime.now(UTC)
    end = to_dt.astimezone(UTC) if to_dt else now
    start = from_dt.astimezone(UTC) if from_dt else (end - timedelta(hours=DEFAULT_QUERY_WINDOW_HOURS))
    if start > end:
        raise OpsLogAuthError(422, "Unprocessable Entity", "'from' must not be later than 'to'.")
    if end - start > timedelta(days=MAX_QUERY_WINDOW_DAYS):
        raise OpsLogAuthError(
            422,
            "Unprocessable Entity",
            f"Time range must not exceed {MAX_QUERY_WINDOW_DAYS} days.",
        )
    return start, end


def _encode_cursor(occurred_at: datetime, log_id: int) -> str:
    raw = f"{rfc3339_millis(occurred_at)}|{log_id}".encode()
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str | None) -> tuple[datetime, int] | None:
    if not cursor:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        ts_raw, id_raw = decoded.split("|", 1)
        if ts_raw.endswith("Z"):
            ts_raw = ts_raw[:-1] + "+00:00"
        return datetime.fromisoformat(ts_raw).astimezone(UTC), int(id_raw)
    except Exception as error:
        raise OpsLogAuthError(422, "Unprocessable Entity", "Invalid pagination cursor.") from error


def _apply_scope_and_filters(stmt, principal: LogPrincipal, flt: LogFilter):
    conditions = [
        log_events.c.occurred_at >= flt.from_dt,
        log_events.c.occurred_at <= flt.to_dt,
    ]
    if not principal.is_admin:
        if flt.course_id is not None and flt.course_id not in principal.scoped_course_ids:
            raise OpsLogAuthError(
                403,
                "Forbidden",
                "Logs for the requested course are outside your scope.",
            )
        conditions.append(log_events.c.course_id.in_(sorted(principal.scoped_course_ids)))
    elif flt.course_id is not None:
        conditions.append(log_events.c.course_id == flt.course_id)

    if flt.producer:
        conditions.append(log_events.c.producer == flt.producer)
    if flt.level:
        conditions.append(log_events.c.level == flt.level)
    if flt.event:
        conditions.append(log_events.c.event == flt.event)
    if flt.request_id:
        conditions.append(log_events.c.request_id == flt.request_id)
    if flt.correlation_id:
        conditions.append(log_events.c.correlation_id == flt.correlation_id)
    if flt.submission_id:
        conditions.append(log_events.c.submission_id == flt.submission_id)
    if flt.io_type:
        conditions.append(log_events.c.fields["io_type"].as_string() == flt.io_type)
    if flt.q:
        pattern = f"%{flt.q}%"
        conditions.append(
            or_(
                log_events.c.event.ilike(pattern),
                cast(log_events.c.fields, String).ilike(pattern),
            )
        )
    return stmt.where(and_(*conditions))


def _row_to_record(row) -> dict[str, Any]:
    occurred = row.occurred_at
    if occurred.tzinfo is None:
        occurred = occurred.replace(tzinfo=UTC)
    return {
        "log_id": int(row.log_id),
        "timestamp": rfc3339_millis(occurred),
        "level": row.level,
        "event": row.event,
        "producer": row.producer,
        "process": row.process,
        "request_id": str(row.request_id) if row.request_id else None,
        "correlation_id": str(row.correlation_id) if row.correlation_id else None,
        "course_id": str(row.course_id) if row.course_id else None,
        "assignment_id": str(row.assignment_id) if row.assignment_id else None,
        "submission_id": str(row.submission_id) if row.submission_id else None,
        "evaluation_run_id": str(row.evaluation_run_id) if row.evaluation_run_id else None,
        "question_version_id": str(row.question_version_id) if row.question_version_id else None,
        "actor_id": str(row.actor_id) if row.actor_id else None,
        "stage": row.stage,
        "fields": dict(row.fields or {}),
        "truncated": bool(row.truncated),
    }


def list_logs(
    reader_engine: Engine,
    principal: LogPrincipal,
    flt: LogFilter,
    *,
    cursor: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    size = max(1, min(limit, 200))
    decoded = _decode_cursor(cursor)
    stmt = select(log_events)
    stmt = _apply_scope_and_filters(stmt, principal, flt)
    if decoded is not None:
        cur_ts, cur_id = decoded
        stmt = stmt.where(
            or_(
                log_events.c.occurred_at < cur_ts,
                and_(log_events.c.occurred_at == cur_ts, log_events.c.log_id < cur_id),
            )
        )
    stmt = stmt.order_by(log_events.c.occurred_at.desc(), log_events.c.log_id.desc()).limit(size + 1)

    with reader_engine.connect() as conn:
        rows = conn.execute(stmt).fetchall()

    has_more = len(rows) > size
    page_rows = rows[:size]
    next_cursor = (
        _encode_cursor(page_rows[-1].occurred_at, page_rows[-1].log_id)
        if has_more and page_rows
        else None
    )
    _audit_logger.info(
        "logs.viewed",
        extra={
            "event": "logs.viewed",
            "actor_id": principal.user_id,
            "course_id": flt.course_id,
            "row_count": len(page_rows),
        },
    )
    return {
        "items": [_row_to_record(r) for r in page_rows],
        "next_cursor": next_cursor,
    }


def get_trace(
    reader_engine: Engine,
    principal: LogPrincipal,
    trace_id: str,
) -> dict[str, Any]:
    canonical = normalize_uuid(trace_id)
    if canonical is None:
        raise OpsLogAuthError(422, "Unprocessable Entity", "trace_id must be a valid UUID.")
    conditions = [
        or_(
            log_events.c.request_id == canonical,
            log_events.c.correlation_id == canonical,
            log_events.c.submission_id == canonical,
        )
    ]
    if not principal.is_admin:
        conditions.append(log_events.c.course_id.in_(sorted(principal.scoped_course_ids)))

    stmt = (
        select(log_events)
        .where(and_(*conditions))
        .order_by(log_events.c.occurred_at.asc(), log_events.c.log_id.asc())
        .limit(MAX_TRACE_ROWS + 1)
    )
    with reader_engine.connect() as conn:
        rows = conn.execute(stmt).fetchall()

    truncated = len(rows) > MAX_TRACE_ROWS
    items = [_row_to_record(r) for r in rows[:MAX_TRACE_ROWS]]
    _audit_logger.info(
        "logs.viewed",
        extra={
            "event": "logs.viewed",
            "actor_id": principal.user_id,
            "correlation_id": canonical,
            "row_count": len(items),
        },
    )
    return {
        "trace_id": canonical,
        "items": items,
        "truncated": truncated,
    }


def get_summary(
    reader_engine: Engine,
    principal: LogPrincipal,
    *,
    module_readiness: Mapping[str, bool] | None = None,
    retention_info_days: int = DEFAULT_RETENTION_INFO_DAYS,
    retention_warn_error_days: int = DEFAULT_RETENTION_WARN_ERROR_DAYS,
    storage_limit_bytes: int = DEFAULT_STORAGE_LIMIT_BYTES,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    cutoff_1h = now - timedelta(hours=1)
    cutoff_24h = now - timedelta(hours=24)

    stmt = select(
        log_events.c.producer,
        func.max(log_events.c.occurred_at).label("last_log_at"),
        func.sum(
            case(
                (and_(log_events.c.level == "warn", log_events.c.occurred_at >= cutoff_1h), 1),
                else_=0,
            )
        ).label("warn_1h"),
        func.sum(
            case(
                (and_(log_events.c.level == "error", log_events.c.occurred_at >= cutoff_1h), 1),
                else_=0,
            )
        ).label("error_1h"),
        func.sum(
            case(
                (and_(log_events.c.level == "warn", log_events.c.occurred_at >= cutoff_24h), 1),
                else_=0,
            )
        ).label("warn_24h"),
        func.sum(
            case(
                (and_(log_events.c.level == "error", log_events.c.occurred_at >= cutoff_24h), 1),
                else_=0,
            )
        ).label("error_24h"),
        func.sum(
            case((log_events.c.occurred_at >= cutoff_24h, 1), else_=0)
        ).label("total_24h"),
    ).group_by(log_events.c.producer)

    if not principal.is_admin:
        stmt = stmt.where(log_events.c.course_id.in_(sorted(principal.scoped_course_ids)))

    by_producer: dict[str, Any] = {}
    with reader_engine.connect() as conn:
        for row in conn.execute(stmt).fetchall():
            by_producer[row.producer] = row
        storage_bytes = measure_storage_bytes(conn)

    readiness = dict(module_readiness or {})
    modules_out = []
    for prod in PRODUCERS_ORDER:
        row = by_producer.get(prod)
        last_at = row.last_log_at if row is not None else None
        if last_at is not None and last_at.tzinfo is None:
            last_at = last_at.replace(tzinfo=UTC)
        silent_minutes = (
            max(0, int((now - last_at).total_seconds() // 60)) if last_at is not None else None
        )
        modules_out.append(
            {
                "producer": prod,
                "last_log_at": rfc3339_millis(last_at) if last_at is not None else None,
                "warn_1h": int(row.warn_1h or 0) if row is not None else 0,
                "error_1h": int(row.error_1h or 0) if row is not None else 0,
                "warn_24h": int(row.warn_24h or 0) if row is not None else 0,
                "error_24h": int(row.error_24h or 0) if row is not None else 0,
                "total_24h": int(row.total_24h or 0) if row is not None else 0,
                "ready": bool(readiness.get(prod, True)),
                "silent_minutes": silent_minutes,
            }
        )

    sink_stats = get_sink_stats()
    return {
        "generated_at": rfc3339_millis(now),
        "modules": modules_out,
        "dropped_total": int(sink_stats["dropped_count"]),
        "db_degraded": bool(sink_stats["db_degraded"]),
        "storage_bytes": storage_bytes,
        "storage_limit_bytes": storage_limit_bytes,
        "retention_info_days": retention_info_days,
        "retention_warn_error_days": retention_warn_error_days,
    }


def export_logs_stream(
    reader_engine: Engine,
    principal: LogPrincipal,
    flt: LogFilter,
    *,
    fmt: str,
    limit: int = 2000,
) -> tuple[Iterator[bytes], str, str]:
    capped_limit = max(1, min(limit, MAX_EXPORT_ROWS))
    stmt = select(log_events)
    stmt = _apply_scope_and_filters(stmt, principal, flt)
    stmt = stmt.order_by(log_events.c.occurred_at.desc(), log_events.c.log_id.desc()).limit(capped_limit)

    with reader_engine.connect() as conn:
        rows = conn.execute(stmt).fetchall()
    records = [_row_to_record(r) for r in rows]

    _audit_logger.info(
        "logs.exported",
        extra={
            "event": "logs.exported",
            "actor_id": principal.user_id,
            "course_id": flt.course_id,
            "format": fmt,
            "row_count": len(records),
        },
    )

    meta_header = (
        f"exported_at={rfc3339_millis(datetime.now(UTC))} "
        f"from={rfc3339_millis(flt.from_dt)} to={rfc3339_millis(flt.to_dt)} "
        f"producer={flt.producer or '*'} level={flt.level or '*'} rows={len(records)}"
    )

    if fmt == "csv":
        def _csv_iter() -> Iterator[bytes]:
            yield f"# {meta_header}\n".encode("utf-8")
            buf = io.StringIO()
            fieldnames = [
                "log_id",
                "timestamp",
                "level",
                "producer",
                "process",
                "event",
                "request_id",
                "correlation_id",
                "course_id",
                "assignment_id",
                "submission_id",
                "evaluation_run_id",
                "question_version_id",
                "actor_id",
                "stage",
                "fields",
                "truncated",
            ]
            writer = csv.DictWriter(buf, fieldnames=fieldnames)
            writer.writeheader()
            yield buf.getvalue().encode("utf-8")
            for rec in records:
                buf.seek(0)
                buf.truncate(0)
                row_dict = dict(rec)
                row_dict["fields"] = json.dumps(rec["fields"], ensure_ascii=False, separators=(",", ":"))
                writer.writerow(row_dict)
                yield buf.getvalue().encode("utf-8")

        return _csv_iter(), "text/csv; charset=utf-8", "ops-logs.csv"

    def _jsonl_iter() -> Iterator[bytes]:
        yield (json.dumps({"_export_meta": meta_header}, ensure_ascii=False) + "\n").encode("utf-8")
        for rec in records:
            yield (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8")

    return _jsonl_iter(), "application/x-ndjson; charset=utf-8", "ops-logs.jsonl"


class ClientLogRateLimiter:
    """In-memory per-user sliding-window rate limiter for browser log ingestion."""

    def __init__(self, *, max_events_per_minute: int = 120) -> None:
        self._limit = max_events_per_minute
        self._lock = threading.Lock()
        self._buckets: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, user_id: str, count: int) -> bool:
        now = time.monotonic()
        cutoff = now - 60.0
        with self._lock:
            bucket = self._buckets[user_id]
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) + count > self._limit:
                return False
            for _ in range(count):
                bucket.append(now)
            return True


_client_limiter = ClientLogRateLimiter()


def ingest_client_logs(
    principal: LogPrincipal,
    lines: list[dict[str, Any]],
) -> dict[str, int]:
    if not _client_limiter.allow(principal.user_id, len(lines)):
        raise OpsLogAuthError(429, "Too Many Requests", "Client log ingestion rate limit exceeded.")
    sink = get_active_sink()
    accepted = 0
    dropped = 0
    for item in lines:
        payload = dict(item)
        payload["producer"] = "B1"
        payload["process"] = "browser"
        payload["actor_id"] = principal.user_id
        payload["event"] = sanitize_text(str(payload.get("event") or "client.log"), max_length=100)
        if sink is not None and sink.enqueue_raw(payload, process="browser", fallback_producer="B1"):
            accepted += 1
        else:
            dropped += 1
    return {"accepted": accepted, "dropped": dropped}
