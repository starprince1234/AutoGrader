"""Tests for Q6 throttled alerts and daily summary emails (no log bodies)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from autograder_host.logs.alerts import MailConfig, check_and_send_alerts, send_daily_summary
from autograder_host.logs.schema import log_events, logs_metadata


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type, _compiler, **_kw) -> str:
    return "JSON"


@compiles(UUID, "sqlite")
def _compile_uuid_sqlite(_type, _compiler, **_kw) -> str:
    return "VARCHAR(36)"


def test_throttled_alerts_and_daily_summary_contain_no_log_bodies() -> None:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    ).execution_options(schema_translate_map={"logs": None})
    with engine.begin() as conn:
        logs_metadata.create_all(conn)

    now = datetime.now(UTC)
    with engine.begin() as conn:
        conn.execute(
            log_events.insert(),
            [
                {
                    "occurred_at": now - timedelta(minutes=5),
                    "level": "error",
                    "event": "runner.sandbox_unavailable",
                    "producer": "B2",
                    "process": "worker",
                    "request_id": str(uuid.uuid4()),
                    "correlation_id": str(uuid.uuid4()),
                    "fields": {"detail": "SENSITIVE_BODY_SHOULD_NOT_APPEAR_IN_MAIL"},
                    "truncated": False,
                    "approx_bytes": 300,
                }
            ],
        )

    sent: list[tuple[str, str]] = []
    cfg = MailConfig(
        enabled=True,
        host="smtp.example.local",
        port=587,
        user="u",
        password="p",
        sender="noreply@example.local",
        recipients=("admin@example.local",),
        use_tls=True,
        ops_url="https://www.91idc.me/app/ops/logs",
    )

    def fake_send(_cfg: MailConfig, *, subject: str, body: str) -> None:
        sent.append((subject, body))

    # First alert check sends 1 email
    keys1 = check_and_send_alerts(engine, engine, cfg=cfg, send_fn=fake_send, now=now)
    assert keys1 == ["alert:dep:B2:runner.sandbox_unavailable"]
    assert len(sent) == 1
    assert "SENSITIVE_BODY_SHOULD_NOT_APPEAR_IN_MAIL" not in sent[0][1]
    assert "https://www.91idc.me/app/ops/logs" in sent[0][1]

    # Second check within 30 minutes is throttled
    keys2 = check_and_send_alerts(
        engine, engine, cfg=cfg, send_fn=fake_send, now=now + timedelta(minutes=10)
    )
    assert keys2 == []
    assert len(sent) == 1

    # Daily summary sends once per 24h
    assert send_daily_summary(engine, engine, cfg=cfg, send_fn=fake_send, now=now) is True
    assert send_daily_summary(
        engine, engine, cfg=cfg, send_fn=fake_send, now=now + timedelta(hours=1)
    ) is False
    assert len(sent) == 2
    assert "B2: warn=0, error=1" in sent[1][1]
    assert "SENSITIVE_BODY_SHOULD_NOT_APPEAR_IN_MAIL" not in sent[1][1]
    engine.dispose()
