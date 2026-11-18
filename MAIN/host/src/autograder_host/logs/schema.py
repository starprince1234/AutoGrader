"""SQLAlchemy Core schema definitions for the ``logs`` PostgreSQL schema."""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

DEFAULT_RETENTION_INFO_DAYS = 7
DEFAULT_RETENTION_WARN_ERROR_DAYS = 30
DEFAULT_STORAGE_LIMIT_BYTES = 128 * 1024 * 1024  # 128 MiB (calibrated in Q0)

logs_metadata = MetaData(schema="logs")

log_events = Table(
    "log_events",
    logs_metadata,
    Column("log_id", BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("level", String(8), nullable=False),
    Column("event", String(100), nullable=False),
    Column("producer", String(16), nullable=False),
    Column("process", String(16), nullable=False),
    Column("request_id", UUID(as_uuid=False), nullable=True),
    Column("correlation_id", UUID(as_uuid=False), nullable=True),
    Column("course_id", UUID(as_uuid=False), nullable=True),
    Column("assignment_id", UUID(as_uuid=False), nullable=True),
    Column("submission_id", UUID(as_uuid=False), nullable=True),
    Column("evaluation_run_id", UUID(as_uuid=False), nullable=True),
    Column("question_version_id", UUID(as_uuid=False), nullable=True),
    Column("actor_id", UUID(as_uuid=False), nullable=True),
    Column("stage", String(64), nullable=True),
    Column("fields", JSONB, nullable=False, server_default=text("'{}'")),
    Column("truncated", Boolean, nullable=False, server_default=text("false")),
    Column("approx_bytes", Integer, nullable=False, server_default=text("0")),
    CheckConstraint("level IN ('info', 'warn', 'error')", name="ck_log_events_level"),
    CheckConstraint(
        "producer IN ('B1', 'B2', 'B3', 'B4', 'B5', 'MAIN')",
        name="ck_log_events_producer",
    ),
    CheckConstraint(
        "process IN ('host', 'worker', 'intake', 'notifier', 'browser')",
        name="ck_log_events_process",
    ),
    Index("idx_log_events_occurred_at", "occurred_at", "log_id"),
    Index("idx_log_events_producer_level_time", "producer", "level", "occurred_at"),
    Index(
        "idx_log_events_request_id",
        "request_id",
        postgresql_where=text("request_id IS NOT NULL"),
    ),
    Index(
        "idx_log_events_correlation_id",
        "correlation_id",
        postgresql_where=text("correlation_id IS NOT NULL"),
    ),
    Index(
        "idx_log_events_submission_id",
        "submission_id",
        postgresql_where=text("submission_id IS NOT NULL"),
    ),
    Index(
        "idx_log_events_course_time",
        "course_id",
        "occurred_at",
        postgresql_where=text("course_id IS NOT NULL"),
    ),
)

log_alert_state = Table(
    "log_alert_state",
    logs_metadata,
    Column("key", String(128), primary_key=True),
    Column("value", JSONB, nullable=False, server_default=text("'{}'")),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
