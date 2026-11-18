"""Tests for Q3 ops logs API, RBAC/course-scope, cursor pagination, export, client ingest, and retention."""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from autograder_host.logs import auth as logs_auth
from autograder_host.logs.retention import run_retention_cleanup
from autograder_host.logs.router import build_ops_logs_router, install_ops_log_exception_handler
from autograder_host.logs.schema import log_events, logs_metadata
from autograder_host.logs.sink import SinkHandler, reset_log_sink_for_tests


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type, _compiler, **_kw) -> str:
    return "JSON"


@compiles(UUID, "sqlite")
def _compile_uuid_sqlite(_type, _compiler, **_kw) -> str:
    return "VARCHAR(36)"


def _make_keypair_and_jwks():
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    jwk_dict = json.loads(jwt.algorithms.OKPAlgorithm.to_jwk(public_key))
    jwk_dict["kid"] = "test-kid-1"
    jwk_dict["use"] = "sig"
    return private_key, {"keys": [jwk_dict]}


def _sign_token(private_key, *, sub: str, roles: list[str], courses: list[str] | None = None) -> str:
    now = int(time.time())
    payload = {
        "iss": "autograder",
        "aud": "autograder-api",
        "sub": sub,
        "jti": str(uuid.uuid4()),
        "iat": now,
        "nbf": now - 5,
        "exp": now + 3600,
        "role": roles,
        "scope": [{"course_id": cid} for cid in (courses or [])],
    }
    return jwt.encode(payload, private_key, algorithm="EdDSA", headers={"kid": "test-kid-1"})


@pytest.fixture()
def ops_env(monkeypatch: pytest.MonkeyPatch):
    private_key, jwks_doc = _make_keypair_and_jwks()
    monkeypatch.setattr(logs_auth, "_read_jwks_document", lambda: jwks_doc)
    monkeypatch.setenv("B4_JWT_ISSUER", "autograder")
    monkeypatch.setenv("B4_JWT_AUDIENCE", "autograder-api")
    logs_auth.clear_jwks_cache_for_tests()
    reset_log_sink_for_tests()

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    engine_translated = engine.execution_options(schema_translate_map={"logs": None})
    with engine_translated.begin() as conn:
        logs_metadata.create_all(conn)

    app = FastAPI()
    app.include_router(build_ops_logs_router(reader_engine=engine_translated))
    install_ops_log_exception_handler(app)

    yield app, engine_translated, private_key

    reset_log_sink_for_tests()
    engine.dispose()


def _seed_sample_logs(engine) -> dict[str, str]:
    now = datetime.now(UTC)
    course_a = str(uuid.uuid4())
    course_b = str(uuid.uuid4())
    corr_1 = str(uuid.uuid4())
    req_1 = str(uuid.uuid4())
    sub_1 = str(uuid.uuid4())

    rows = [
        {
            "occurred_at": now - timedelta(minutes=10),
            "level": "info",
            "event": "platform.host.started",
            "producer": "MAIN",
            "process": "host",
            "request_id": None,
            "correlation_id": None,
            "course_id": None,
            "submission_id": None,
            "fields": {"module_count": 4},
            "truncated": False,
            "approx_bytes": 300,
        },
        {
            "occurred_at": now - timedelta(minutes=5),
            "level": "info",
            "event": "submission.created",
            "producer": "B1",
            "process": "browser",
            "request_id": req_1,
            "correlation_id": corr_1,
            "course_id": course_a,
            "submission_id": sub_1,
            "fields": {"io_type": "stdin"},
            "truncated": False,
            "approx_bytes": 320,
        },
        {
            "occurred_at": now - timedelta(minutes=4),
            "level": "warn",
            "event": "evaluation.run.started",
            "producer": "B2",
            "process": "worker",
            "request_id": None,
            "correlation_id": corr_1,
            "course_id": course_a,
            "submission_id": sub_1,
            "fields": {"io_type": "stdin"},
            "truncated": False,
            "approx_bytes": 320,
        },
        {
            "occurred_at": now - timedelta(minutes=3),
            "level": "error",
            "event": "evaluation.run.failed",
            "producer": "B2",
            "process": "worker",
            "request_id": None,
            "correlation_id": corr_1,
            "course_id": course_b,
            "submission_id": None,
            "fields": {"io_type": "callable", "error_category": "timeout"},
            "truncated": False,
            "approx_bytes": 350,
        },
    ]
    with engine.begin() as conn:
        conn.execute(log_events.insert(), rows)
    return {"course_a": course_a, "course_b": course_b, "corr_1": corr_1, "req_1": req_1, "sub_1": sub_1}


def test_rbac_and_course_scope_filtering(ops_env) -> None:
    app, engine, pem = ops_env
    ids = _seed_sample_logs(engine)
    client = TestClient(app)

    admin_tok = _sign_token(pem, sub=str(uuid.uuid4()), roles=["admin"])
    teacher_tok = _sign_token(pem, sub=str(uuid.uuid4()), roles=["teacher"], courses=[ids["course_a"]])
    student_tok = _sign_token(pem, sub=str(uuid.uuid4()), roles=["student"], courses=[ids["course_a"]])

    # Unauthenticated -> 401
    assert client.get("/api/v1/ops/logs").status_code == 401

    # Student -> 403 on all ops endpoints
    for method, path, kwargs in [
        ("GET", "/api/v1/ops/logs", {}),
        ("GET", f"/api/v1/ops/logs/trace/{ids['corr_1']}", {}),
        ("GET", "/api/v1/ops/logs/summary", {}),
        ("POST", "/api/v1/ops/logs/exports", {"json": {"format": "jsonl"}}),
    ]:
        res = client.request(method, path, headers={"Authorization": f"Bearer {student_tok}"}, **kwargs)
        assert res.status_code == 403

    # Admin sees all 4 events (including platform.host.started with course_id=None)
    res_admin = client.get("/api/v1/ops/logs", headers={"Authorization": f"Bearer {admin_tok}"})
    assert res_admin.status_code == 200
    assert len(res_admin.json()["items"]) == 4

    # Teacher sees ONLY course_a events (2 events)
    res_teacher = client.get("/api/v1/ops/logs", headers={"Authorization": f"Bearer {teacher_tok}"})
    assert res_teacher.status_code == 200
    items = res_teacher.json()["items"]
    assert len(items) == 2
    assert all(item["course_id"] == ids["course_a"] for item in items)

    # Teacher requesting course_b -> 403
    res_cross = client.get(
        f"/api/v1/ops/logs?course_id={ids['course_b']}",
        headers={"Authorization": f"Bearer {teacher_tok}"},
    )
    assert res_cross.status_code == 403


def test_cursor_pagination_trace_summary_and_export_with_audit(ops_env, caplog) -> None:
    app, engine, pem = ops_env
    ids = _seed_sample_logs(engine)
    client = TestClient(app)
    admin_tok = _sign_token(pem, sub=str(uuid.uuid4()), roles=["admin"])
    headers = {"Authorization": f"Bearer {admin_tok}"}

    # Cursor pagination
    p1 = client.get("/api/v1/ops/logs?limit=2", headers=headers).json()
    assert len(p1["items"]) == 2
    assert p1["next_cursor"] is not None
    p2 = client.get(f"/api/v1/ops/logs?limit=2&cursor={p1['next_cursor']}", headers=headers).json()
    assert len(p2["items"]) == 2
    assert p2["next_cursor"] is None
    assert {r["log_id"] for r in p1["items"]}.isdisjoint({r["log_id"] for r in p2["items"]})

    # Trace timeline (chronological)
    trace = client.get(f"/api/v1/ops/logs/trace/{ids['corr_1']}", headers=headers).json()
    assert trace["trace_id"] == ids["corr_1"]
    assert [r["producer"] for r in trace["items"]] == ["B1", "B2", "B2"]

    # Summary
    summary = client.get("/api/v1/ops/logs/summary", headers=headers).json()
    by_prod = {m["producer"]: m for m in summary["modules"]}
    assert by_prod["B2"]["warn_1h"] == 1
    assert by_prod["B2"]["error_1h"] == 1
    assert summary["retention_info_days"] == 7
    assert summary["retention_warn_error_days"] == 30

    # Export JSONL and CSV + verify audit events logged
    caplog.set_level(logging.INFO, logger="autograder_host.logs.audit")
    exp_jsonl = client.post(
        "/api/v1/ops/logs/exports",
        headers=headers,
        json={"format": "jsonl", "limit": 2},
    )
    assert exp_jsonl.status_code == 200
    lines = [line for line in exp_jsonl.text.strip().splitlines() if line]
    assert len(lines) == 3  # 1 meta header + 2 rows

    exp_csv = client.post(
        "/api/v1/ops/logs/exports",
        headers=headers,
        json={"format": "csv"},
    )
    assert exp_csv.status_code == 200
    assert exp_csv.text.startswith("# exported_at=")
    assert "platform.host.started" in exp_csv.text

    audit_events = [getattr(r, "event", None) for r in caplog.records]
    assert "logs.viewed" in audit_events
    assert "logs.exported" in audit_events


def test_client_log_ingestion_and_redaction(ops_env) -> None:
    app, engine, pem = ops_env
    written: list[dict] = []
    sink = SinkHandler(process="host", write_fn=lambda batch: written.extend(batch))
    # Register sink as active global sink
    from autograder_host.logs import sink as sink_mod

    sink_mod._GLOBAL_SINK = sink
    try:
        client = TestClient(app)
        student_tok = _sign_token(pem, sub=str(uuid.uuid4()), roles=["student"])
        res = client.post(
            "/api/v1/logs/client",
            headers={"Authorization": f"Bearer {student_tok}"},
            json={
                "lines": [
                    {
                        "timestamp": "2026-11-18T10:00:00.000Z",
                        "level": "error",
                        "event": "api.request.failed",
                        "password": "leak",
                        "status_code": 500,
                    }
                ]
            },
        )
        assert res.status_code == 202
        assert res.json() == {"accepted": 1, "dropped": 0}
        sink.flush()
        assert len(written) == 1
        assert written[0]["producer"] == "B1"
        assert written[0]["process"] == "browser"
        assert "password" not in written[0]["fields"]
        assert written[0]["fields"]["status_code"] == 500
    finally:
        reset_log_sink_for_tests()


def test_retention_and_capacity_cleanup(ops_env) -> None:
    _, engine, _ = ops_env
    now = datetime.now(UTC)
    rows = [
        # Old info (10d) -> deleted by time
        {
            "occurred_at": now - timedelta(days=10),
            "level": "info",
            "event": "old.info",
            "producer": "MAIN",
            "process": "host",
            "fields": {},
            "truncated": False,
            "approx_bytes": 500,
        },
        # Old error (35d) -> deleted by time
        {
            "occurred_at": now - timedelta(days=35),
            "level": "error",
            "event": "old.error",
            "producer": "MAIN",
            "process": "host",
            "fields": {},
            "truncated": False,
            "approx_bytes": 500,
        },
        # Recent info -> deleted by capacity cap before error
        {
            "occurred_at": now - timedelta(hours=2),
            "level": "info",
            "event": "recent.info",
            "producer": "MAIN",
            "process": "host",
            "fields": {},
            "truncated": False,
            "approx_bytes": 600,
        },
        # Recent error -> kept when within cap after info deleted
        {
            "occurred_at": now - timedelta(hours=1),
            "level": "error",
            "event": "recent.error",
            "producer": "MAIN",
            "process": "host",
            "fields": {},
            "truncated": False,
            "approx_bytes": 400,
        },
    ]
    with engine.begin() as conn:
        conn.execute(log_events.insert(), rows)

    report = run_retention_cleanup(engine, info_days=7, warn_error_days=30, max_bytes=500)
    assert report.deleted_info == 1
    assert report.deleted_warn_error == 1
    assert report.deleted_capacity == 1
    assert report.storage_bytes <= 500

    with engine.connect() as conn:
        remaining = [r[0] for r in conn.execute(log_events.select().with_only_columns(log_events.c.event)).fetchall()]
    assert remaining == ["recent.error"]
