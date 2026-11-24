"""Non-blocking, bounded-queue batch log sink writing to PostgreSQL ``logs.log_events``."""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from .provision import resolve_logs_dsn
from .redaction import normalize_log_record, normalize_raw_dict
from .schema import log_events

_sink_logger = logging.getLogger("autograder_host.logs.sink")

DEFAULT_QUEUE_CAPACITY = 2048
DEFAULT_BATCH_SIZE = 100
DEFAULT_FLUSH_INTERVAL_SEC = 1.0

_TARGET_LOGGERS = (
    "",
    "autograder_host",
    "autograder_b2",
    "autograder_b3",
    "autograder_b4",
    "autograder_b5",
)


class SinkHandler(logging.Handler):
    """In-process logging.Handler -> bounded queue -> background batch INSERT.

    Hard guarantees:
    - Never blocks the caller thread (O(1) bounded enqueue).
    - Never raises exceptions into application code.
    - Drops the oldest item when the queue is full and increments ``dropped_count``.
    - Degrades gracefully when the database is unavailable without crashing.
    """

    def __init__(
        self,
        *,
        process: str = "host",
        writer_dsn: str | None = None,
        writer_engine: Engine | None = None,
        capacity: int = DEFAULT_QUEUE_CAPACITY,
        batch_size: int = DEFAULT_BATCH_SIZE,
        flush_interval: float = DEFAULT_FLUSH_INTERVAL_SEC,
        write_fn: Callable[[list[dict[str, Any]]], None] | None = None,
    ) -> None:
        super().__init__(level=logging.INFO)
        self._process = process
        self._capacity = max(1, capacity)
        self._batch_size = max(1, batch_size)
        self._flush_interval = max(0.05, flush_interval)
        self._queue: deque[dict[str, Any]] = deque()
        self._lock = threading.Lock()
        self._dropped_count = 0
        self._db_degraded = False
        self._db_degraded_reason: str | None = None
        self._degraded_logged = False
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()

        self._owns_engine = False
        self._engine: Engine | None = writer_engine
        if self._engine is None and writer_dsn:
            try:
                self._engine = create_engine(
                    writer_dsn,
                    pool_pre_ping=True,
                    pool_size=1,
                    max_overflow=1,
                    future=True,
                )
                self._owns_engine = True
            except Exception:
                self._engine = None
                self._db_degraded = True

        self._write_fn = write_fn or self._default_write_batch
        self._worker: threading.Thread | None = None
        if self._engine is not None or write_fn is not None:
            self._worker = threading.Thread(
                target=self._run_loop,
                name=f"autograder-log-sink-{process}",
                daemon=True,
            )
            self._worker.start()

    @property
    def process_name(self) -> str:
        return self._process

    @property
    def dropped_count(self) -> int:
        with self._lock:
            return self._dropped_count

    @property
    def db_degraded(self) -> bool:
        return self._db_degraded

    @property
    def db_degraded_reason(self) -> str | None:
        """Why the last write failed, for operators. Never contains a log row."""
        return self._db_degraded_reason

    @property
    def db_sink_enabled(self) -> bool:
        return self._engine is not None or self._worker is not None

    @property
    def queue_depth(self) -> int:
        with self._lock:
            return len(self._queue)

    def enqueue_normalized(self, item: dict[str, Any] | None) -> bool:
        if item is None:
            return False
        if not self.db_sink_enabled:
            return False
        with self._lock:
            if len(self._queue) >= self._capacity:
                self._queue.popleft()
                self._dropped_count += 1
            self._queue.append(item)
            should_wake = len(self._queue) >= self._batch_size
        if should_wake:
            self._wake_event.set()
        return True

    def enqueue_raw(
        self,
        raw: dict[str, Any],
        *,
        process: str | None = None,
        fallback_producer: str = "MAIN",
    ) -> bool:
        try:
            normalized = normalize_raw_dict(
                raw,
                process=process or self._process,
                fallback_producer=fallback_producer,
            )
            return self.enqueue_normalized(normalized)
        except Exception:
            return False

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if record.levelno < logging.INFO:
                return
            normalized = normalize_log_record(record, process=self._process)
            self.enqueue_normalized(normalized)
        except Exception:
            # Never propagate exceptions from logging.
            return

    def _default_write_batch(self, batch: list[dict[str, Any]]) -> None:
        if self._engine is None or not batch:
            return
        rows = []
        for item in batch:
            fields_obj = item.get("fields") or {}
            fields_json = json.dumps(fields_obj, ensure_ascii=False, separators=(",", ":"))
            approx_bytes = len(fields_json.encode("utf-8")) + len(item["event"]) + 256
            rows.append(
                {
                    "occurred_at": item["occurred_at"],
                    "level": item["level"],
                    "event": item["event"],
                    "producer": item["producer"],
                    "process": item["process"],
                    "request_id": item.get("request_id"),
                    "correlation_id": item.get("correlation_id"),
                    "course_id": item.get("course_id"),
                    "assignment_id": item.get("assignment_id"),
                    "submission_id": item.get("submission_id"),
                    "evaluation_run_id": item.get("evaluation_run_id"),
                    "question_version_id": item.get("question_version_id"),
                    "actor_id": item.get("actor_id"),
                    "stage": item.get("stage"),
                    "fields": fields_obj,
                    "truncated": bool(item.get("truncated", False)),
                    "approx_bytes": approx_bytes,
                }
            )
        with self._engine.begin() as conn:
            conn.execute(log_events.insert(), rows)

    def flush(self) -> None:
        """Drain queued records in batches. Never raises.

        Two behaviours matter to whoever reads the log center's "degraded" banner:

        * a batch that fails is **put back** and counted when the queue cannot hold it, because a
          transient database error must not silently lose rows;
        * the reason is remembered and reported once per episode, so "why is it orange?" has an
          answer (the ``logs.sink.degraded`` event) instead of only a banner.

        Because the failed batch stays queued, the worker retries it on the next tick: the flag
        therefore means "there are rows not yet in the database", and it clears as soon as they
        are written -- not "some write failed at some point", which is what made the banner
        linger after a hiccup.
        """
        if not self.db_sink_enabled:
            return
        while True:
            with self._lock:
                if not self._queue:
                    break
                batch: list[dict[str, Any]] = []
                while self._queue and len(batch) < self._batch_size:
                    batch.append(self._queue.popleft())
            if not batch:
                break
            try:
                self._write_fn(batch)
                self._clear_degraded()
            except Exception as error:
                self._mark_degraded(error)
                self._requeue(batch)
                # Best-effort: do not loop endlessly on a dead DB during one flush.
                break

    def _requeue(self, batch: list[dict[str, Any]]) -> None:
        """Return a failed batch to the front of the queue, dropping the oldest if it cannot fit."""
        with self._lock:
            overflow = len(batch) + len(self._queue) - self._capacity
            keep = batch
            if overflow > 0:
                dropped_from_batch = min(overflow, len(batch))
                self._dropped_count += dropped_from_batch
                keep = batch[dropped_from_batch:]
                overflow -= dropped_from_batch
            while overflow > 0 and self._queue:
                self._queue.popleft()
                self._dropped_count += 1
                overflow -= 1
            self._queue.extendleft(reversed(keep))

    def _mark_degraded(self, error: BaseException) -> None:
        self._db_degraded = True
        self._db_degraded_reason = f"{type(error).__name__}: {error}"[:200]
        if self._degraded_logged:
            return
        self._degraded_logged = True
        # Logged once per episode: this record travels through the same sink, so repeating it
        # while the database is down would push real traffic out of the queue.
        _sink_logger.warning(
            "logs.sink.degraded",
            extra={
                "event": "logs.sink.degraded",
                "outcome": "degraded",
                "error_type": type(error).__name__,
                "error_detail": str(error)[:160],
            },
        )

    def _clear_degraded(self) -> None:
        self._db_degraded = False
        self._db_degraded_reason = None
        self._degraded_logged = False

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            self._wake_event.wait(timeout=self._flush_interval)
            self._wake_event.clear()
            self.flush()
        self.flush()

    def close(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        if self._worker is not None and self._worker.is_alive():
            self._worker.join(timeout=2.0)
        if self._owns_engine and self._engine is not None:
            try:
                self._engine.dispose()
            except Exception:
                pass
        super().close()


_GLOBAL_LOCK = threading.Lock()
_GLOBAL_SINK: SinkHandler | None = None


def attach_log_sink(
    *,
    process: str = "host",
    writer_dsn: str | None = None,
    writer_engine: Engine | None = None,
) -> SinkHandler:
    """Idempotently attach the process-wide ``SinkHandler`` to root and module loggers."""
    global _GLOBAL_SINK
    with _GLOBAL_LOCK:
        if _GLOBAL_SINK is None:
            resolved_dsn = writer_dsn or resolve_logs_dsn("LOGS_WRITER_DATABASE_URL")
            _GLOBAL_SINK = SinkHandler(
                process=process,
                writer_dsn=resolved_dsn,
                writer_engine=writer_engine,
            )
        sink = _GLOBAL_SINK
        for logger_name in _TARGET_LOGGERS:
            target = logging.getLogger(logger_name)
            if not any(isinstance(h, SinkHandler) for h in target.handlers):
                target.addHandler(sink)
            if target.level == logging.NOTSET or target.level > logging.INFO:
                if logger_name != "":
                    # Keep module logger level at INFO or lower if unset.
                    if target.level == logging.NOTSET:
                        target.setLevel(logging.INFO)
        return sink


def get_active_sink() -> SinkHandler | None:
    return _GLOBAL_SINK


def get_sink_stats() -> dict[str, Any]:
    sink = _GLOBAL_SINK
    if sink is None:
        return {
            "dropped_count": 0,
            "db_degraded": False,
            "db_degraded_reason": None,
            "db_sink_enabled": False,
            "queue_depth": 0,
        }
    return {
        "dropped_count": sink.dropped_count,
        "db_degraded": sink.db_degraded,
        "db_degraded_reason": sink.db_degraded_reason,
        "db_sink_enabled": sink.db_sink_enabled,
        "queue_depth": sink.queue_depth,
    }


def flush_log_sink(timeout: float = 2.0) -> None:
    sink = _GLOBAL_SINK
    if sink is not None:
        deadline = time.monotonic() + timeout
        sink.flush()
        while sink.queue_depth > 0 and time.monotonic() < deadline and not sink.db_degraded:
            time.sleep(0.02)
            sink.flush()


def reset_log_sink_for_tests() -> None:
    global _GLOBAL_SINK
    with _GLOBAL_LOCK:
        if _GLOBAL_SINK is not None:
            for logger_name in _TARGET_LOGGERS:
                target = logging.getLogger(logger_name)
                for h in list(target.handlers):
                    if isinstance(h, SinkHandler):
                        target.removeHandler(h)
            _GLOBAL_SINK.close()
            _GLOBAL_SINK = None
