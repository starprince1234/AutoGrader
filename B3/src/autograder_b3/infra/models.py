"""SQLAlchemy models. Every object lives in the ``b3`` schema and nowhere else.

Boundary rules encoded here (``docs/architecture/boundaries.md`` §3):

* ``course_id`` / ``section_id`` / actor ids are plain UUID columns **without**
  foreign keys: B4 owns those tables and cross-schema FKs are forbidden. Scope is
  validated in the domain layer against the caller's verified token, never by a
  database join.
* ``b3.blobs`` (fixture bytes) is protected: only the internal role has a grant.
* Statistics tables carry **no** foreign key to ``question_versions``: an event
  stream must stay replayable on its own, and a statistic must never become
  unreconstructable because a version row was archived.

``b3.reference_attestations`` is deliberately **not** a protected table: it holds the
fact, the counts and the digest a verifier reported, never the reference answer or an
expected output. It is readable by the ordinary role and writable only by the internal
one, because computing the content digest a verifier is attesting to requires reading
protection-class content.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA = "b3"

LANGUAGE_VALUES = ("python", "bash")
VERSION_STATUS_VALUES = ("draft", "published", "archived")
ASSIGNMENT_STATUS_VALUES = ("open", "archived")
MOUNT_VALUES = ("input", "expected_output")
#: Consumption outcomes that get a counter row (``events.consumer.ConsumptionOutcome``).
CONSUMPTION_OUTCOME_VALUES = (
    "accepted",
    "duplicate_event",
    "duplicate_run",
    "rejected",
)
MAX_TAGS = 20
TAG_PATTERN = r"^[a-z0-9][a-z0-9-]*$"
TAG_PATTERN_SQL = r"^[a-z0-9][a-z0-9-]{0,39}$"

PATH_PATTERN = r"^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*$"
SHA256_PATTERN = r"^[0-9a-f]{64}$"


class Base(DeclarativeBase):
    __table_args__ = {"schema": SCHEMA}


def _sha256_check(column: str, constraint: str) -> CheckConstraint:
    return CheckConstraint(f"{column} ~ '{SHA256_PATTERN}'", name=constraint)


class Question(Base):
    __tablename__ = "questions"
    __table_args__ = (
        CheckConstraint(
            "language IN ('python', 'bash')", name="ck_questions_language"
        ),
        CheckConstraint(
            "status IN ('draft', 'published', 'archived')", name="ck_questions_status"
        ),
        CheckConstraint("length(title) BETWEEN 1 AND 300", name="ck_questions_title_length"),
        CheckConstraint("length(stem_markdown) >= 1", name="ck_questions_stem_not_empty"),
        CheckConstraint(
            "jsonb_typeof(tags) = 'array'",
            name="ck_questions_tags_is_array",
        ),
        CheckConstraint(
            f"NOT jsonb_path_exists(tags, '$[*] ? (@.type() != \"string\" "
            f"|| !(@ like_regex \"{TAG_PATTERN_SQL}\"))')",
            name="ck_questions_tags_normalised",
        ),
        CheckConstraint(f"jsonb_array_length(tags) <= {MAX_TAGS}", name="ck_questions_tags_count"),
        Index("ix_questions_course_id", "course_id"),
        Index("ix_questions_status", "status"),
        Index("ix_questions_tags", "tags", postgresql_using="gin"),
        {"schema": SCHEMA},
    )

    question_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    course_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    stem_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class QuestionVersion(Base):
    __tablename__ = "question_versions"
    __table_args__ = (
        UniqueConstraint("question_id", "version", name="uq_question_versions_question_id_version"),
        CheckConstraint(
            "language IN ('python', 'bash')", name="ck_question_versions_language"
        ),
        CheckConstraint(
            "status IN ('draft', 'published', 'archived')", name="ck_question_versions_status"
        ),
        CheckConstraint("version >= 1", name="ck_question_versions_version_positive"),
        CheckConstraint(
            "status = 'draft' OR published_at IS NOT NULL",
            name="ck_question_versions_published_at_required",
        ),
        CheckConstraint(
            "status <> 'archived' OR archived_at IS NOT NULL",
            name="ck_question_versions_archived_at_required",
        ),
        CheckConstraint(
            "status <> 'published' OR (content_digest IS NOT NULL AND package_digest IS NOT NULL)",
            name="ck_question_versions_published_has_digests",
        ),
        CheckConstraint("max_files BETWEEN 1 AND 100", name="ck_question_versions_max_files"),
        CheckConstraint("max_total_bytes >= 1", name="ck_question_versions_max_total_bytes"),
        CheckConstraint(
            "revision_note IS NULL OR length(revision_note) BETWEEN 1 AND 500",
            name="ck_question_versions_revision_note_length",
        ),
        CheckConstraint(
            "jsonb_typeof(revision_diff) = 'object'",
            name="ck_question_versions_revision_diff_object",
        ),
        Index("ix_question_versions_question_id", "question_id"),
        Index("ix_question_versions_status", "status"),
        {"schema": SCHEMA},
    )

    question_version_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    question_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("b3.questions.question_id", name="fk_question_versions_question_id"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    language: Mapped[str] = mapped_column(String(16), nullable=False)
    stem_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    adapter_manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    package_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    max_files: Mapped[int] = mapped_column(Integer, nullable=False)
    max_total_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Why this version exists, as told by the teacher. Recorded on a new draft version
    #: only; the contract does not declare a way to submit it yet (see
    #: ``docs/api/contract.md`` -> 未成文的契约缺口).
    revision_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: Structural delta against the version this one supersedes. Counts, digests and
    #: presence flags only -- never stem text, a reference answer or an expected output,
    #: so it stays safe to serialize once the contract declares a place for it.
    #: ``none_as_null`` matters: without it SQLAlchemy binds Python ``None`` as the JSON
    #: value ``null``, which is *not* SQL NULL and fails the object-type check below.
    revision_diff: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class QuestionAsset(Base):
    """Stem graphic. Bytes are served through B3's controlled asset route."""

    __tablename__ = "question_assets"
    __table_args__ = (
        UniqueConstraint(
            "question_id", "content_sha256", name="uq_question_assets_question_id_content_sha256"
        ),
        _sha256_check("content_sha256", "ck_question_assets_content_sha256"),
        CheckConstraint("size_bytes > 0", name="ck_question_assets_size_positive"),
        Index("ix_question_assets_question_id", "question_id"),
        Index("ix_question_assets_question_version_id", "question_version_id"),
        {"schema": SCHEMA},
    )

    asset_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    question_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("b3.questions.question_id", name="fk_question_assets_question_id"),
        nullable=False,
    )
    question_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    alt_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReferenceAnswer(Base):
    """PROTECTED. No grant for the ordinary question-bank role (acceptance 3-10-3)."""

    __tablename__ = "reference_answers"
    __table_args__ = (
        UniqueConstraint(
            "question_version_id",
            "language",
            name="uq_reference_answers_question_version_id_language",
        ),
        CheckConstraint("language IN ('python', 'bash')", name="ck_reference_answers_language"),
        CheckConstraint("length(body) >= 1", name="ck_reference_answers_body_not_empty"),
        {"schema": SCHEMA},
    )

    reference_answer_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    question_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey(
            "b3.question_versions.question_version_id",
            name="fk_reference_answers_question_version_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    language: Mapped[str] = mapped_column(String(16), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TestSuite(Base):
    __tablename__ = "test_suites"
    __table_args__ = (
        UniqueConstraint(
            "question_version_id", name="uq_test_suites_question_version_id"
        ),
        {"schema": SCHEMA},
    )

    test_suite_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    question_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey(
            "b3.question_versions.question_version_id",
            name="fk_test_suites_question_version_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TestCase(Base):
    """PROTECTED: inputs, expected stdout/stderr and exit codes live here."""

    __tablename__ = "test_cases"
    __table_args__ = (
        UniqueConstraint("test_suite_id", "ordinal", name="uq_test_cases_test_suite_id_ordinal"),
        CheckConstraint("ordinal >= 0", name="ck_test_cases_ordinal_non_negative"),
        CheckConstraint("weight > 0", name="ck_test_cases_weight_positive"),
        CheckConstraint("length(name) BETWEEN 1 AND 200", name="ck_test_cases_name_length"),
        Index("ix_test_cases_test_suite_id", "test_suite_id"),
        {"schema": SCHEMA},
    )

    test_case_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    test_suite_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey(
            "b3.test_suites.test_suite_id",
            name="fk_test_cases_test_suite_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    question_version_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    visible_to_student: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    stdin: Mapped[str | None] = mapped_column(Text, nullable=True)
    args: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    expected_stdout: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_stderr: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Blob(Base):
    """PROTECTED content-addressed store for fixture bytes."""

    __tablename__ = "blobs"
    __table_args__ = (
        _sha256_check("content_sha256", "ck_blobs_content_sha256"),
        CheckConstraint("size_bytes > 0", name="ck_blobs_size_positive"),
        {"schema": SCHEMA},
    )

    content_sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Fixture(Base):
    """Fixture metadata; the digest is bound into the package at publish time."""

    __tablename__ = "fixtures"
    __table_args__ = (
        UniqueConstraint(
            "question_version_id", "path", "mount", name="uq_fixtures_question_version_id_path_mount"
        ),
        _sha256_check("content_sha256", "ck_fixtures_content_sha256"),
        CheckConstraint("mount IN ('input', 'expected_output')", name="ck_fixtures_mount"),
        CheckConstraint(f"path ~ '{PATH_PATTERN}'", name="ck_fixtures_path_pattern"),
        CheckConstraint("size_bytes >= 0", name="ck_fixtures_size_non_negative"),
        Index("ix_fixtures_question_version_id", "question_version_id"),
        {"schema": SCHEMA},
    )

    fixture_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    question_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey(
            "b3.question_versions.question_version_id",
            name="fk_fixtures_question_version_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    path: Mapped[str] = mapped_column(String(200), nullable=False)
    mount: Mapped[str] = mapped_column(String(16), nullable=False)
    content_sha256: Mapped[str] = mapped_column(
        String(64), ForeignKey("b3.blobs.content_sha256", name="fk_fixtures_content_sha256"), nullable=False
    )
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReferenceAttestation(Base):
    """A verifier's digest-bound claim that a reference answer reproduces its outputs.

    B3 never executes anything (``RQ-B-3-10`` boundary), so the fact that a reference
    answer produces the expected output can only be recorded, not established, here.
    ``publish`` requires a row whose ``content_digest`` equals the digest B3 recomputes
    from the version's own content, so an attestation can never be carried over to
    different content: editing the content changes the digest and invalidates it.

    Keyed by ``(question_version_id, content_digest)`` rather than by version alone, so
    the record of an earlier verification survives an edit instead of being overwritten.
    """

    __tablename__ = "reference_attestations"
    __table_args__ = (
        _sha256_check("content_digest", "ck_reference_attestations_content_digest"),
        CheckConstraint(
            "length(verifier) BETWEEN 1 AND 200", name="ck_reference_attestations_verifier_length"
        ),
        CheckConstraint(
            "cases_total BETWEEN 1 AND 1000", name="ck_reference_attestations_cases_total"
        ),
        CheckConstraint(
            "cases_passed BETWEEN 0 AND cases_total",
            name="ck_reference_attestations_cases_passed",
        ),
        Index("ix_reference_attestations_question_version_id", "question_version_id"),
        {"schema": SCHEMA},
    )

    question_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey(
            "b3.question_versions.question_version_id",
            name="fk_reference_attestations_question_version_id",
            ondelete="CASCADE",
        ),
        primary_key=True,
    )
    content_digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    attestation_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, unique=True)
    verifier: Mapped[str] = mapped_column(String(200), nullable=False)
    cases_total: Mapped[int] = mapped_column(Integer, nullable=False)
    cases_passed: Mapped[int] = mapped_column(Integer, nullable=False)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ConsumptionCounter(Base):
    """Per-outcome delivery counters for the statistics consumer.

    Added because ``event_inbox`` alone cannot tell "no attempts yet" from "every
    attempt was rejected": a rejected event is never written to the inbox, so the
    rejection path was invisible in the database and only existed in logs. These
    counters move on every path (ADR-0006 §6 keeps the DLQ behaviour unchanged).
    """

    __tablename__ = "consumption_counters"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('accepted', 'duplicate_event', 'duplicate_run', 'rejected')",
            name="ck_consumption_counters_outcome",
        ),
        CheckConstraint("delivery_count >= 0", name="ck_consumption_counters_delivery_count"),
        {"schema": SCHEMA},
    )

    outcome: Mapped[str] = mapped_column(String(32), primary_key=True)
    delivery_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    last_occurred_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Assignment(Base):
    __tablename__ = "assignments"
    __table_args__ = (
        CheckConstraint("length(title) BETWEEN 1 AND 200", name="ck_assignments_title_length"),
        CheckConstraint("status IN ('open', 'archived')", name="ck_assignments_status"),
        Index("ix_assignments_course_id", "course_id"),
        Index("ix_assignments_section_id", "section_id"),
        {"schema": SCHEMA},
    )

    assignment_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    course_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    section_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AssignmentItem(Base):
    __tablename__ = "assignment_items"
    __table_args__ = (
        UniqueConstraint(
            "assignment_id", "question_version_id", name="uq_assignment_items_assignment_id_question_version_id"
        ),
        UniqueConstraint("assignment_id", "ordinal", name="uq_assignment_items_assignment_id_ordinal"),
        CheckConstraint("ordinal >= 0", name="ck_assignment_items_ordinal_non_negative"),
        Index("ix_assignment_items_question_version_id", "question_version_id"),
        {"schema": SCHEMA},
    )

    assignment_item_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    assignment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("b3.assignments.assignment_id", name="fk_assignment_items_assignment_id"),
        nullable=False,
    )
    question_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey(
            "b3.question_versions.question_version_id",
            name="fk_assignment_items_question_version_id",
        ),
        nullable=False,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EventInbox(Base):
    """Durable dedupe store: one row per ``event_id`` (ADR-0006 §4).

    ``payload`` keeps the *whitelist-validated, de-identified* payload only, which
    is what makes replay-from-events possible without re-reading NATS.
    """

    __tablename__ = "event_inbox"
    __table_args__ = (
        UniqueConstraint("evaluation_run_id", name="uq_event_inbox_evaluation_run_id"),
        CheckConstraint("producer = 'B2'", name="ck_event_inbox_producer"),
        CheckConstraint("length(event_type) > 0", name="ck_event_inbox_event_type"),
        Index("ix_event_inbox_occurred_at", "occurred_at"),
        Index("ix_event_inbox_question_version_id", "question_version_id"),
        #: Serves the bounded keyset scan the windowed statistics query performs:
        #: (question_version_id = ...) AND (occurred_at, event_id) > (cursor).
        Index(
            "ix_event_inbox_question_version_id_occurred_at",
            "question_version_id",
            "occurred_at",
            "event_id",
        ),
        {"schema": SCHEMA},
    )

    event_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(200), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    producer: Mapped[str] = mapped_column(String(8), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    correlation_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    causation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    evaluation_run_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    question_version_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EventOutbox(Base):
    """Outbox written in the same transaction as the business change (ADR-0006 §5)."""

    __tablename__ = "event_outbox"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_event_outbox_event_id"),
        CheckConstraint("producer = 'B3'", name="ck_event_outbox_producer"),
        Index("ix_event_outbox_published_at", "published_at"),
        {"schema": SCHEMA},
    )

    outbox_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    event_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    event_type: Mapped[str] = mapped_column(String(200), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    correlation_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    causation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    producer: Mapped[str] = mapped_column(String(8), nullable=False, default="B3")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class QuestionVersionStats(Base):
    """Pre-aggregation per question version. Contains counters only (no identity)."""

    __tablename__ = "question_version_stats"
    __table_args__ = (
        CheckConstraint("attempt_count >= 0", name="ck_question_version_stats_attempt_count"),
        CheckConstraint("pass_known_count >= 0", name="ck_question_version_stats_pass_known"),
        CheckConstraint("pass_count >= 0", name="ck_question_version_stats_pass_count"),
        CheckConstraint("pass_count <= pass_known_count", name="ck_question_version_stats_pass_le_known"),
        CheckConstraint(
            "unbucketed_attempt_count >= 0", name="ck_question_version_stats_unbucketed"
        ),
        {"schema": SCHEMA},
    )

    question_version_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    attempt_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    pass_known_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    pass_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    unbucketed_attempt_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    first_occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class QuestionVersionScoreBucket(Base):
    __tablename__ = "question_version_score_buckets"
    __table_args__ = (
        CheckConstraint("bucket_count >= 0", name="ck_question_version_score_buckets_count"),
        {"schema": SCHEMA},
    )

    question_version_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    bucket_label: Mapped[str] = mapped_column(String(32), primary_key=True)
    bucket_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class QuestionVersionFailureCategory(Base):
    __tablename__ = "question_version_failure_categories"
    __table_args__ = (
        CheckConstraint("category_count >= 0", name="ck_question_version_failure_categories_count"),
        {"schema": SCHEMA},
    )

    question_version_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    failed_category: Mapped[str] = mapped_column(String(64), primary_key=True)
    category_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


ALL_MODELS = (
    Question,
    QuestionVersion,
    QuestionAsset,
    ReferenceAnswer,
    TestSuite,
    TestCase,
    Blob,
    Fixture,
    ReferenceAttestation,
    Assignment,
    AssignmentItem,
    EventInbox,
    EventOutbox,
    ConsumptionCounter,
    QuestionVersionStats,
    QuestionVersionScoreBucket,
    QuestionVersionFailureCategory,
)

#: Tables the ordinary question-bank role must never read (acceptance 3-10-3).
#: ``reference_attestations`` is intentionally absent: it carries a digest and counts,
#: never the content those counts are about.
PROTECTED_TABLES = ("reference_answers", "test_suites", "test_cases", "blobs", "fixtures")
