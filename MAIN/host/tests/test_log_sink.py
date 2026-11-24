"""Tests for Q2 unified log sink, normalization, secondary redaction, and degradation."""

from __future__ import annotations

import importlib.metadata
import json
import logging
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from autograder_host.logs.redaction import normalize_log_record, normalize_raw_dict
from autograder_host.logs.sink import SinkHandler

REPO_ROOT = Path(__file__).resolve().parents[3]
LOG_RECORD_SCHEMA = json.loads(
    (REPO_ROOT / "MAIN" / "contracts" / "schemas" / "log-record.schema.json").read_text(
        encoding="utf-8"
    )
)
VALIDATOR = Draft202012Validator(LOG_RECORD_SCHEMA)


def _without_occurred_at(item: dict) -> dict:
    return {k: v for k, v in item.items() if k != "occurred_at"}


def test_normalization_matches_contract_schema() -> None:
    req_id = str(uuid.uuid4())
    corr_id = str(uuid.uuid4())
    sub_id = str(uuid.uuid4())

    b3_raw = {
        "timestamp": "2026-11-18T10:00:00.123Z",
        "level": "WARNING",
        "producer": "B3",
        "event": "question.compile.failed",
        "request_id": req_id,
        "correlation_id": corr_id,
        "io_type": "stdin",
        "reason": "invalid [redacted] fixture",
    }
    norm = normalize_raw_dict(b3_raw, process="host")
    assert norm is not None
    assert norm["level"] == "warn"
    assert norm["producer"] == "B3"
    assert norm["process"] == "host"
    assert norm["request_id"] == req_id
    assert norm["correlation_id"] == corr_id
    assert norm["fields"]["io_type"] == "stdin"
    assert "[REDACTED]" in norm["fields"]["reason"]
    assert "[redacted]" not in norm["fields"]["reason"]
    VALIDATOR.validate(_without_occurred_at(norm))

    # Debug records are never persisted
    assert normalize_raw_dict({"level": "debug", "event": "x"}, process="host") is None

    # B2 LogRecord with b2_event
    rec = logging.LogRecord(
        name="autograder_b2.evaluation.orchestrator",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="evaluation.run.completed",
        args=(),
        exc_info=None,
    )
    rec.b2_event = {
        "event": "evaluation.run.completed",
        "correlation_id": corr_id,
        "submission_id": sub_id,
        "io_type": "callable",
        "duration_ms": 42,
        "outcome": "passed",
    }
    norm_b2 = normalize_log_record(rec, process="worker")
    assert norm_b2 is not None
    assert norm_b2["producer"] == "B2"
    assert norm_b2["process"] == "worker"
    assert norm_b2["submission_id"] == sub_id
    assert norm_b2["fields"]["io_type"] == "callable"
    VALIDATOR.validate(_without_occurred_at(norm_b2))


def test_secondary_redaction_blocks_sensitive_data_and_injection() -> None:
    secret_pw = "SuperSecretPass123!"
    secret_email = "student@example.edu"
    secret_dsn = "postgresql://user:s3cr3t@10.0.0.5:5432/autograder"
    secret_jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTYifQ.sig_abcdef12345"
    source_snippet = "def solve(x):\n    return x + 1"

    raw = {
        "level": "error",
        "event": "test.leak\r\ninjection",
        "producer": "B2",
        "request_id": "not-a-uuid-source-code",
        "password": secret_pw,
        "reference_answer": "print(42)",
        "hidden_cases": [{"expected_stdout": "42"}],
        "fixture_content": "secret fixture",
        "unknown_arbitrary_key": "should_be_dropped",
        "detail": f"auth Bearer {secret_jwt} for {secret_email} via {secret_dsn}",
        "reason": source_snippet,
    }
    norm = normalize_raw_dict(raw, process="host")
    assert norm is not None
    dumped = json.dumps(_without_occurred_at(norm), ensure_ascii=False)

    assert "request_id" not in norm
    assert "unknown_arbitrary_key" not in norm["fields"]
    assert "password" not in norm["fields"]
    assert "reference_answer" not in norm["fields"]
    assert "hidden_cases" not in norm["fields"]
    assert "fixture_content" not in norm["fields"]
    assert secret_pw not in dumped
    assert secret_email not in dumped
    assert "s3cr3t" not in dumped
    assert secret_jwt not in dumped
    assert "def solve" not in dumped
    assert "\r" not in norm["event"]
    assert "\n" not in norm["event"]


def test_a_failed_batch_is_retried_not_lost_and_says_why() -> None:
    """A transient database error must not silently eat a batch of log rows.

    The "degraded" banner in the log center is driven by this flag, and the old behaviour lost
    the batch it could not write while showing nothing but the banner: no reason, no row count,
    no way to tell a hiccup from an outage.
    """
    written: list[dict] = []
    failures = {"count": 1}

    def flaky_writer(batch: list[dict]) -> None:
        if failures["count"] > 0:
            failures["count"] -= 1
            raise RuntimeError("connection reset by peer")
        written.extend(batch)

    sink = SinkHandler(
        process="host", capacity=10, batch_size=2, flush_interval=0.05, write_fn=flaky_writer
    )
    try:
        sink.enqueue_raw({"level": "info", "event": "evt.retry"}, fallback_producer="MAIN")
        sink.flush()
        assert sink.db_degraded is True
        assert "RuntimeError" in (sink.db_degraded_reason or "")
        assert sink.dropped_count == 0

        # The same rows are still queued, so the next flush writes them -- nothing was lost.
        sink.flush()
        assert [row["event"] for row in written] == ["evt.retry"]
        assert sink.db_degraded is False
        assert sink.db_degraded_reason is None
    finally:
        sink.close()


def test_degraded_means_rows_are_still_pending_not_that_a_write_once_failed() -> None:
    """What the banner means now.

    Before, a failed batch was dropped and the flag only cleared on the *next* successful write,
    so on a quiet platform the banner described a failure that had already passed. Now the batch
    stays queued: the flag is true exactly while rows are undelivered, and the retry that writes
    them clears it.
    """
    written: list[dict] = []
    broken = {"on": True}

    def writer(batch: list[dict]) -> None:
        if broken["on"]:
            raise OSError("connection refused")
        written.extend(batch)

    sink = SinkHandler(
        process="host", capacity=10, batch_size=10, flush_interval=0.05, write_fn=writer
    )
    try:
        sink.enqueue_raw({"level": "info", "event": "evt.pending"}, fallback_producer="MAIN")
        sink.flush()
        assert sink.db_degraded is True
        # The row was not lost: it is queued and will be retried on the next tick.
        assert sink.queue_depth == 1
        assert sink.dropped_count == 0

        broken["on"] = False
        sink.flush()
        assert [row["event"] for row in written] == ["evt.pending"]
        assert sink.queue_depth == 0
        assert sink.db_degraded is False
    finally:
        sink.close()


def test_sink_drops_oldest_when_full_and_degrades_on_db_failure() -> None:
    written: list[dict] = []

    def failing_writer(batch: list[dict]) -> None:
        raise RuntimeError("database connection refused")

    sink = SinkHandler(
        process="host",
        capacity=3,
        batch_size=10,
        flush_interval=0.05,
        write_fn=failing_writer,
    )
    try:
        for idx in range(5):
            ok = sink.enqueue_raw({"level": "info", "event": f"evt.{idx}"}, fallback_producer="MAIN")
            assert ok is True
        assert sink.dropped_count == 2
        sink.flush()
        assert sink.db_degraded is True
    finally:
        sink.close()

    # Now verify normal batch flush succeeds
    sink_ok = SinkHandler(
        process="host",
        capacity=10,
        batch_size=2,
        flush_interval=0.05,
        write_fn=lambda batch: written.extend(batch),
    )
    try:
        sink_ok.enqueue_raw({"level": "info", "event": "evt.a"}, fallback_producer="MAIN")
        sink_ok.enqueue_raw({"level": "warn", "event": "evt.b"}, fallback_producer="MAIN")
        deadline = time.monotonic() + 1.0
        while len(written) < 2 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert [row["event"] for row in written] == ["evt.a", "evt.b"]
        assert sink_ok.db_degraded is False
    finally:
        sink_ok.close()


def test_request_id_middleware_generates_and_echoes_uuid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importlib.metadata, "entry_points", lambda **kwargs: [])
    monkeypatch.delenv("AUTOGRADER_MODULES", raising=False)
    from autograder_host.main import create_app

    app = create_app()
    client = TestClient(app)

    res1 = client.get("/health/live")
    assert res1.status_code == 200
    generated = res1.headers.get("x-request-id")
    assert generated is not None
    assert str(uuid.UUID(generated)) == generated

    custom = str(uuid.uuid4())
    res2 = client.get("/health/live", headers={"X-Request-ID": custom})
    assert res2.status_code == 200
    assert res2.headers.get("x-request-id") == custom
