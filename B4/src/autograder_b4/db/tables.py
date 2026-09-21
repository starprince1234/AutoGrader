"""SQLAlchemy Core definitions for the `b4` schema.

Every table is schema-qualified: B4 owns `b4` and nothing else, and an unqualified name
would resolve through `search_path`, which is exactly the coupling the boundary rules
forbid. These definitions are checked against the live database by
`tests/test_schema_conformance.py`, so a migration and a query can never drift apart
silently.

Physical DDL authority remains `alembic/versions/**` (docs/README.md).
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Integer,
    LargeBinary,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID

SCHEMA = "b4"

#: `timestamptz` at millisecond precision, matching docs/database/table-standards.md §3.
TIMESTAMPTZ = DateTime(timezone=True)

metadata = MetaData(schema=SCHEMA)

users = Table(
    "users",
    metadata,
    Column("user_id", PGUUID(as_uuid=True), primary_key=True),
    Column("username", Text, nullable=False, unique=True),
    Column("display_name", Text, nullable=False),
    Column("student_number", Text, nullable=True, unique=True),
    Column("email", Text, nullable=True),
    Column("is_active", Boolean, nullable=False, server_default="true"),
    Column("deactivated_at", TIMESTAMPTZ, nullable=True),
    Column("created_at", TIMESTAMPTZ, nullable=False, server_default=func.now()),
    Column("updated_at", TIMESTAMPTZ, nullable=False, server_default=func.now()),
)

courses = Table(
    "courses",
    metadata,
    Column("course_id", PGUUID(as_uuid=True), primary_key=True),
    Column("course_code", Text, nullable=False, unique=True),
    Column("title", Text, nullable=False),
    Column("created_by", PGUUID(as_uuid=True), nullable=True),
    Column("is_active", Boolean, nullable=False, server_default="true"),
    Column("created_at", TIMESTAMPTZ, nullable=False, server_default=func.now()),
    Column("updated_at", TIMESTAMPTZ, nullable=False, server_default=func.now()),
)

sections = Table(
    "sections",
    metadata,
    Column("section_id", PGUUID(as_uuid=True), primary_key=True),
    Column("course_id", PGUUID(as_uuid=True), nullable=False),
    Column("section_code", Text, nullable=False),
    Column("term", Text, nullable=True),
    Column("created_by", PGUUID(as_uuid=True), nullable=True),
    Column("created_at", TIMESTAMPTZ, nullable=False, server_default=func.now()),
    Column("updated_at", TIMESTAMPTZ, nullable=False, server_default=func.now()),
)

roles = Table(
    "roles",
    metadata,
    Column("role_id", PGUUID(as_uuid=True), primary_key=True),
    Column("user_id", PGUUID(as_uuid=True), nullable=False),
    Column("role", Text, nullable=False),
    Column("course_id", PGUUID(as_uuid=True), nullable=True),
    Column("granted_by", PGUUID(as_uuid=True), nullable=True),
    Column("created_at", TIMESTAMPTZ, nullable=False, server_default=func.now()),
)

credentials = Table(
    "credentials",
    metadata,
    Column("credential_id", PGUUID(as_uuid=True), primary_key=True),
    Column("user_id", PGUUID(as_uuid=True), nullable=False, unique=True),
    Column("password_hash", Text, nullable=False),
    Column("hash_algorithm", Text, nullable=False),
    Column("created_at", TIMESTAMPTZ, nullable=False, server_default=func.now()),
    Column("updated_at", TIMESTAMPTZ, nullable=False, server_default=func.now()),
)

sessions = Table(
    "sessions",
    metadata,
    Column("session_id", PGUUID(as_uuid=True), primary_key=True),
    Column("user_id", PGUUID(as_uuid=True), nullable=False),
    Column("family_id", PGUUID(as_uuid=True), nullable=False),
    Column("refresh_token_hash", Text, nullable=False),
    Column("issued_at", TIMESTAMPTZ, nullable=False, server_default=func.now()),
    Column("expires_at", TIMESTAMPTZ, nullable=False),
    Column("rotated_at", TIMESTAMPTZ, nullable=True),
    Column("revoked_at", TIMESTAMPTZ, nullable=True),
    Column("replaced_by", PGUUID(as_uuid=True), nullable=True),
    Column("revoked_reason", Text, nullable=True),
    Column("created_at", TIMESTAMPTZ, nullable=False, server_default=func.now()),
    Column("updated_at", TIMESTAMPTZ, nullable=False, server_default=func.now()),
)

roster_imports = Table(
    "roster_imports",
    metadata,
    Column("roster_import_id", PGUUID(as_uuid=True), primary_key=True),
    Column("course_id", PGUUID(as_uuid=True), nullable=False),
    Column("section_id", PGUUID(as_uuid=True), nullable=False),
    Column("status", Text, nullable=False),
    Column("filename", Text, nullable=False),
    Column("media_type", Text, nullable=False),
    Column("content_sha256", Text, nullable=False),
    Column("size_bytes", BigInteger, nullable=False),
    Column("detected_columns", JSONB, nullable=False, server_default="'[]'::jsonb"),
    Column("proposed_mapping", JSONB, nullable=False, server_default="'{}'::jsonb"),
    Column("confirmed_mapping", JSONB, nullable=True),
    Column("parse_detail", Text, nullable=True),
    Column("accepted_count", Integer, nullable=False, server_default="0"),
    Column("rejected_count", Integer, nullable=False, server_default="0"),
    Column("idempotency_key", Text, nullable=True),
    Column("created_by", PGUUID(as_uuid=True), nullable=True),
    Column("created_at", TIMESTAMPTZ, nullable=False, server_default=func.now()),
    Column("updated_at", TIMESTAMPTZ, nullable=False, server_default=func.now()),
    Column("committed_at", TIMESTAMPTZ, nullable=True),
)

roster_import_files = Table(
    "roster_import_files",
    metadata,
    Column("file_id", PGUUID(as_uuid=True), primary_key=True),
    Column("roster_import_id", PGUUID(as_uuid=True), nullable=False),
    Column("content", LargeBinary, nullable=False),
    Column("created_at", TIMESTAMPTZ, nullable=False, server_default=func.now()),
)

roster_import_rows = Table(
    "roster_import_rows",
    metadata,
    Column("row_id", PGUUID(as_uuid=True), primary_key=True),
    Column("roster_import_id", PGUUID(as_uuid=True), nullable=False),
    Column("row_number", Integer, nullable=False),
    Column("status", Text, nullable=False),
    Column("row_values", JSONB, nullable=False),
    Column("reason", Text, nullable=True),
    Column("created_at", TIMESTAMPTZ, nullable=False, server_default=func.now()),
)

enrollments = Table(
    "enrollments",
    metadata,
    Column("enrollment_id", PGUUID(as_uuid=True), primary_key=True),
    Column("section_id", PGUUID(as_uuid=True), nullable=False),
    Column("user_id", PGUUID(as_uuid=True), nullable=True),
    Column("student_number", Text, nullable=False),
    Column("display_name", Text, nullable=False),
    Column("source", Text, nullable=False),
    Column("roster_import_id", PGUUID(as_uuid=True), nullable=True),
    Column("is_active", Boolean, nullable=False, server_default="true"),
    Column("created_at", TIMESTAMPTZ, nullable=False, server_default=func.now()),
    Column("updated_at", TIMESTAMPTZ, nullable=False, server_default=func.now()),
)

audit_records = Table(
    "audit_records",
    metadata,
    Column("audit_id", PGUUID(as_uuid=True), primary_key=True),
    Column("occurred_at", TIMESTAMPTZ, nullable=False, server_default=func.now()),
    Column("actor_user_id", PGUUID(as_uuid=True), nullable=True),
    Column("actor_roles", ARRAY(Text), nullable=False, server_default="ARRAY[]::text[]"),
    Column("action", Text, nullable=False),
    Column("outcome", Text, nullable=False),
    Column("target_type", Text, nullable=True),
    Column("target_id", PGUUID(as_uuid=True), nullable=True),
    Column("course_id", PGUUID(as_uuid=True), nullable=True),
    Column("section_id", PGUUID(as_uuid=True), nullable=True),
    Column("request_id", Text, nullable=True),
    Column("correlation_id", Text, nullable=True),
    Column("change_digest", Text, nullable=True),
    Column("detail", Text, nullable=True),
)

outbox_events = Table(
    "outbox_events",
    metadata,
    Column("outbox_id", PGUUID(as_uuid=True), primary_key=True),
    Column("event_id", PGUUID(as_uuid=True), nullable=False, unique=True),
    Column("event_type", Text, nullable=False),
    Column("schema_version", Integer, nullable=False),
    Column("occurred_at", TIMESTAMPTZ, nullable=False),
    Column("correlation_id", PGUUID(as_uuid=True), nullable=False),
    Column("causation_id", PGUUID(as_uuid=True), nullable=True),
    Column("producer", Text, nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("published_at", TIMESTAMPTZ, nullable=True),
    Column("attempts", Integer, nullable=False, server_default="0"),
    Column("last_error", Text, nullable=True),
    Column("created_at", TIMESTAMPTZ, nullable=False, server_default=func.now()),
)

inbox_events = Table(
    "inbox_events",
    metadata,
    Column("inbox_id", PGUUID(as_uuid=True), primary_key=True),
    Column("consumer", Text, nullable=False),
    Column("event_id", PGUUID(as_uuid=True), nullable=False),
    Column("subject", Text, nullable=False),
    Column("received_at", TIMESTAMPTZ, nullable=False, server_default=func.now()),
    UniqueConstraint("consumer", "event_id", name="uq_inbox_events_consumer_event_id"),
)

dlq_messages = Table(
    "dlq_messages",
    metadata,
    Column("dlq_id", PGUUID(as_uuid=True), primary_key=True),
    Column("original_stream", Text, nullable=False),
    Column("original_consumer", Text, nullable=True),
    Column("original_subject", Text, nullable=False),
    Column("original_sequence", BigInteger, nullable=True),
    Column("event_id", PGUUID(as_uuid=True), nullable=True),
    Column("reason", Text, nullable=False),
    Column("delivery_count", Integer, nullable=False, server_default="0"),
    Column("envelope", JSONB, nullable=True),
    Column("created_at", TIMESTAMPTZ, nullable=False, server_default=func.now()),
    Column("replayed_at", TIMESTAMPTZ, nullable=True),
    Column("replay_attempts", Integer, nullable=False, server_default="0"),
)

login_attempts = Table(
    "login_attempts",
    metadata,
    Column("attempt_id", PGUUID(as_uuid=True), primary_key=True),
    Column("principal_key", Text, nullable=False),
    Column("client_key", Text, nullable=True),
    Column("succeeded", Boolean, nullable=False),
    Column("occurred_at", TIMESTAMPTZ, nullable=False, server_default=func.now()),
)

#: The version table Alembic owns, declared here so schema-conformance checks know it
#: is expected rather than an unmanaged hand-made table.
alembic_version = Table(
    "alembic_version_b4",
    metadata,
    Column("version_num", Text, primary_key=True),
)

ALL_TABLES = (
    users,
    courses,
    sections,
    roles,
    credentials,
    sessions,
    roster_imports,
    roster_import_files,
    roster_import_rows,
    enrollments,
    audit_records,
    outbox_events,
    inbox_events,
    dlq_messages,
    login_attempts,
)
