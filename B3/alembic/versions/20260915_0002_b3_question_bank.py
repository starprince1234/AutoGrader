"""B3 question bank: full business schema.

Revision ID: 20260915_0002
Revises: 20260915_0001
Create Date: 2026-09-15

One migration, one concern: stand up the B3 question bank inside ``b3`` and make
version immutability a database guarantee rather than an application convention.

Contents:

1. business tables (question / version / asset / protected content / fixture /
   assignment / inbox / outbox / statistics);
2. triggers that refuse in-place edits of published content and deletes of
   published versions (SQLSTATE ``23001``, mapped to ``409`` by the API);
3. least-privilege grants: the ordinary question-bank role reads no protected
   table, the internal role owns the compile/publish path.

Boundary notes: no object outside ``b3`` is created or referenced; ``course_id`` /
``section_id`` are plain UUID columns on purpose (B4 owns those tables, and
cross-schema foreign keys are forbidden). B3 owns no scoring, evidence or reporting
object: those belong to B2, and this migration introduces none of them.
"""

from __future__ import annotations

from alembic import op

revision = "20260915_0002"
down_revision = "20260915_0001"
branch_labels = None
depends_on = None

SHA256 = r"^[0-9a-f]{64}$"
PATH_PATTERN = r"^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*$"

TABLES = (
    "questions",
    "question_versions",
    "question_assets",
    "reference_answers",
    "test_suites",
    "test_cases",
    "blobs",
    "fixtures",
    "assignments",
    "assignment_items",
    "event_inbox",
    "event_outbox",
    "question_version_stats",
    "question_version_score_buckets",
    "question_version_failure_categories",
)

#: Tables the ordinary question-bank role must never read (acceptance 3-10-3).
PROTECTED_TABLES = ("reference_answers", "test_suites", "test_cases", "blobs", "fixtures")


def upgrade() -> None:
    _create_questions()
    _create_versions()
    _create_assets()
    _create_protected_content()
    _create_assignments()
    _create_events()
    _create_statistics()
    _create_immutability_triggers()
    _grant_privileges()


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_question_versions_guard ON b3.question_versions")
    for table in ("reference_answers", "test_suites", "test_cases", "fixtures"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_guard ON b3.{table}")
    op.execute("DROP FUNCTION IF EXISTS b3.tg_question_versions_guard()")
    op.execute("DROP FUNCTION IF EXISTS b3.tg_protected_content_guard()")
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS b3.{table}")


# --------------------------------------------------------------------------
# tables
# --------------------------------------------------------------------------


def _create_questions() -> None:
    op.execute(
        """
        CREATE TABLE b3.questions (
            question_id uuid,
            CONSTRAINT pk_questions PRIMARY KEY (question_id),
            course_id uuid NOT NULL,
            title text NOT NULL,
            stem_markdown text NOT NULL,
            language varchar(16) NOT NULL,
            status varchar(16) NOT NULL DEFAULT 'draft',
            tags jsonb NOT NULL DEFAULT '[]'::jsonb,
            created_by uuid,
            created_at timestamptz(3) NOT NULL,
            updated_at timestamptz(3) NOT NULL,
            deleted_at timestamptz(3),
            CONSTRAINT ck_questions_language CHECK (language IN ('python', 'bash')),
            CONSTRAINT ck_questions_status CHECK (status IN ('draft', 'published', 'archived')),
            CONSTRAINT ck_questions_title_length CHECK (length(title) BETWEEN 1 AND 300),
            CONSTRAINT ck_questions_stem_not_empty CHECK (length(stem_markdown) >= 1)
        )
        """
    )
    op.execute("CREATE INDEX ix_questions_course_id ON b3.questions (course_id)")
    op.execute("CREATE INDEX ix_questions_status ON b3.questions (status)")
    op.execute(
        "COMMENT ON COLUMN b3.questions.course_id IS "
        "'B4-owned course scope; deliberately not a foreign key (cross-schema FKs are forbidden)'"
    )


def _create_versions() -> None:
    op.execute(
        """
        CREATE TABLE b3.question_versions (
            question_version_id uuid,
            CONSTRAINT pk_question_versions PRIMARY KEY (question_version_id),
            question_id uuid NOT NULL,
            version integer NOT NULL,
            status varchar(16) NOT NULL DEFAULT 'draft',
            language varchar(16) NOT NULL,
            stem_markdown text NOT NULL,
            adapter_manifest jsonb NOT NULL,
            content_digest varchar(64),
            package_digest varchar(64),
            max_files integer NOT NULL,
            max_total_bytes integer NOT NULL,
            created_by uuid,
            created_at timestamptz(3) NOT NULL,
            updated_at timestamptz(3) NOT NULL,
            published_at timestamptz(3),
            archived_at timestamptz(3),
            CONSTRAINT fk_question_versions_question_id
                FOREIGN KEY (question_id) REFERENCES b3.questions (question_id),
            CONSTRAINT uq_question_versions_question_id_version UNIQUE (question_id, version),
            CONSTRAINT ck_question_versions_language CHECK (language IN ('python', 'bash')),
            CONSTRAINT ck_question_versions_status
                CHECK (status IN ('draft', 'published', 'archived')),
            CONSTRAINT ck_question_versions_version_positive CHECK (version >= 1),
            CONSTRAINT ck_question_versions_published_at_required
                CHECK (status = 'draft' OR published_at IS NOT NULL),
            CONSTRAINT ck_question_versions_archived_at_required
                CHECK (status <> 'archived' OR archived_at IS NOT NULL),
            CONSTRAINT ck_question_versions_published_has_digests
                CHECK (status <> 'published' OR (content_digest IS NOT NULL AND package_digest IS NOT NULL)),
            CONSTRAINT ck_question_versions_max_files CHECK (max_files BETWEEN 1 AND 100),
            CONSTRAINT ck_question_versions_max_total_bytes CHECK (max_total_bytes >= 1)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_question_versions_question_id ON b3.question_versions (question_id)"
    )
    op.execute("CREATE INDEX ix_question_versions_status ON b3.question_versions (status)")


def _create_assets() -> None:
    op.execute(
        f"""
        CREATE TABLE b3.question_assets (
            asset_id uuid,
            CONSTRAINT pk_question_assets PRIMARY KEY (asset_id),
            question_id uuid NOT NULL,
            question_version_id uuid,
            content_type varchar(100) NOT NULL,
            size_bytes integer NOT NULL,
            content_sha256 varchar(64) NOT NULL,
            alt_text text,
            content bytea NOT NULL,
            created_at timestamptz(3) NOT NULL,
            CONSTRAINT fk_question_assets_question_id
                FOREIGN KEY (question_id) REFERENCES b3.questions (question_id),
            CONSTRAINT uq_question_assets_question_id_content_sha256
                UNIQUE (question_id, content_sha256),
            CONSTRAINT ck_question_assets_content_sha256 CHECK (content_sha256 ~ '{SHA256}'),
            CONSTRAINT ck_question_assets_size_positive CHECK (size_bytes > 0)
        )
        """
    )
    op.execute("CREATE INDEX ix_question_assets_question_id ON b3.question_assets (question_id)")
    op.execute(
        "CREATE INDEX ix_question_assets_question_version_id "
        "ON b3.question_assets (question_version_id)"
    )


def _create_protected_content() -> None:
    op.execute(
        """
        CREATE TABLE b3.reference_answers (
            reference_answer_id uuid,
            CONSTRAINT pk_reference_answers PRIMARY KEY (reference_answer_id),
            question_version_id uuid NOT NULL,
            language varchar(16) NOT NULL,
            body text NOT NULL,
            created_at timestamptz(3) NOT NULL,
            CONSTRAINT fk_reference_answers_question_version_id
                FOREIGN KEY (question_version_id)
                REFERENCES b3.question_versions (question_version_id) ON DELETE CASCADE,
            CONSTRAINT uq_reference_answers_question_version_id_language
                UNIQUE (question_version_id, language),
            CONSTRAINT ck_reference_answers_language CHECK (language IN ('python', 'bash')),
            CONSTRAINT ck_reference_answers_body_not_empty CHECK (length(body) >= 1)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE b3.test_suites (
            test_suite_id uuid,
            CONSTRAINT pk_test_suites PRIMARY KEY (test_suite_id),
            question_version_id uuid NOT NULL,
            created_at timestamptz(3) NOT NULL,
            CONSTRAINT fk_test_suites_question_version_id
                FOREIGN KEY (question_version_id)
                REFERENCES b3.question_versions (question_version_id) ON DELETE CASCADE,
            CONSTRAINT uq_test_suites_question_version_id UNIQUE (question_version_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE b3.test_cases (
            test_case_id uuid,
            CONSTRAINT pk_test_cases PRIMARY KEY (test_case_id),
            test_suite_id uuid NOT NULL,
            question_version_id uuid NOT NULL,
            ordinal integer NOT NULL,
            name text NOT NULL,
            weight double precision NOT NULL DEFAULT 1,
            visible_to_student boolean NOT NULL DEFAULT false,
            stdin text,
            args jsonb NOT NULL DEFAULT '[]'::jsonb,
            expected_stdout text,
            expected_stderr text,
            expected_exit_code integer,
            created_at timestamptz(3) NOT NULL,
            CONSTRAINT fk_test_cases_test_suite_id
                FOREIGN KEY (test_suite_id) REFERENCES b3.test_suites (test_suite_id)
                ON DELETE CASCADE,
            CONSTRAINT uq_test_cases_test_suite_id_ordinal UNIQUE (test_suite_id, ordinal),
            CONSTRAINT ck_test_cases_ordinal_non_negative CHECK (ordinal >= 0),
            CONSTRAINT ck_test_cases_weight_positive CHECK (weight > 0),
            CONSTRAINT ck_test_cases_name_length CHECK (length(name) BETWEEN 1 AND 200)
        )
        """
    )
    op.execute("CREATE INDEX ix_test_cases_test_suite_id ON b3.test_cases (test_suite_id)")
    op.execute(
        f"""
        CREATE TABLE b3.blobs (
            content_sha256 varchar(64),
            CONSTRAINT pk_blobs PRIMARY KEY (content_sha256),
            content bytea NOT NULL,
            size_bytes integer NOT NULL,
            created_at timestamptz(3) NOT NULL,
            CONSTRAINT ck_blobs_content_sha256 CHECK (content_sha256 ~ '{SHA256}'),
            CONSTRAINT ck_blobs_size_positive CHECK (size_bytes > 0)
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE b3.fixtures (
            fixture_id uuid,
            CONSTRAINT pk_fixtures PRIMARY KEY (fixture_id),
            question_version_id uuid NOT NULL,
            path varchar(200) NOT NULL,
            mount varchar(16) NOT NULL,
            content_sha256 varchar(64) NOT NULL,
            size_bytes integer NOT NULL,
            created_at timestamptz(3) NOT NULL,
            CONSTRAINT fk_fixtures_question_version_id
                FOREIGN KEY (question_version_id)
                REFERENCES b3.question_versions (question_version_id) ON DELETE CASCADE,
            CONSTRAINT fk_fixtures_content_sha256
                FOREIGN KEY (content_sha256) REFERENCES b3.blobs (content_sha256),
            CONSTRAINT uq_fixtures_question_version_id_path_mount
                UNIQUE (question_version_id, path, mount),
            CONSTRAINT ck_fixtures_content_sha256 CHECK (content_sha256 ~ '{SHA256}'),
            CONSTRAINT ck_fixtures_mount CHECK (mount IN ('input', 'expected_output')),
            CONSTRAINT ck_fixtures_path_pattern CHECK (path ~ '{PATH_PATTERN}'),
            CONSTRAINT ck_fixtures_size_non_negative CHECK (size_bytes >= 0)
        )
        """
    )
    op.execute("CREATE INDEX ix_fixtures_question_version_id ON b3.fixtures (question_version_id)")
    op.execute(
        "COMMENT ON TABLE b3.reference_answers IS "
        "'PROTECTED: readable only by the internal role; never in a public DTO, event or log'"
    )
    op.execute(
        "COMMENT ON TABLE b3.test_cases IS "
        "'PROTECTED: hidden inputs and expected outputs; internal role only'"
    )
    op.execute(
        "COMMENT ON TABLE b3.blobs IS "
        "'PROTECTED: content-addressed fixture bytes; internal role only'"
    )


def _create_assignments() -> None:
    op.execute(
        """
        CREATE TABLE b3.assignments (
            assignment_id uuid,
            CONSTRAINT pk_assignments PRIMARY KEY (assignment_id),
            course_id uuid NOT NULL,
            section_id uuid NOT NULL,
            title text NOT NULL,
            status varchar(16) NOT NULL DEFAULT 'open',
            due_at timestamptz(3),
            created_by uuid,
            created_at timestamptz(3) NOT NULL,
            updated_at timestamptz(3) NOT NULL,
            CONSTRAINT ck_assignments_title_length CHECK (length(title) BETWEEN 1 AND 200),
            CONSTRAINT ck_assignments_status CHECK (status IN ('open', 'archived'))
        )
        """
    )
    op.execute("CREATE INDEX ix_assignments_course_id ON b3.assignments (course_id)")
    op.execute("CREATE INDEX ix_assignments_section_id ON b3.assignments (section_id)")
    op.execute(
        """
        CREATE TABLE b3.assignment_items (
            assignment_item_id uuid,
            CONSTRAINT pk_assignment_items PRIMARY KEY (assignment_item_id),
            assignment_id uuid NOT NULL,
            question_version_id uuid NOT NULL,
            ordinal integer NOT NULL,
            created_at timestamptz(3) NOT NULL,
            CONSTRAINT fk_assignment_items_assignment_id
                FOREIGN KEY (assignment_id) REFERENCES b3.assignments (assignment_id),
            CONSTRAINT fk_assignment_items_question_version_id
                FOREIGN KEY (question_version_id)
                REFERENCES b3.question_versions (question_version_id),
            CONSTRAINT uq_assignment_items_assignment_id_question_version_id
                UNIQUE (assignment_id, question_version_id),
            CONSTRAINT uq_assignment_items_assignment_id_ordinal UNIQUE (assignment_id, ordinal),
            CONSTRAINT ck_assignment_items_ordinal_non_negative CHECK (ordinal >= 0)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_assignment_items_question_version_id "
        "ON b3.assignment_items (question_version_id)"
    )


def _create_events() -> None:
    op.execute(
        """
        CREATE TABLE b3.event_inbox (
            event_id uuid,
            CONSTRAINT pk_event_inbox PRIMARY KEY (event_id),
            event_type varchar(200) NOT NULL,
            schema_version integer NOT NULL,
            producer varchar(8) NOT NULL,
            occurred_at timestamptz(3) NOT NULL,
            correlation_id uuid NOT NULL,
            causation_id uuid,
            evaluation_run_id uuid NOT NULL,
            question_version_id uuid NOT NULL,
            payload jsonb NOT NULL,
            payload_digest varchar(64) NOT NULL,
            received_at timestamptz(3) NOT NULL,
            processed_at timestamptz(3),
            CONSTRAINT uq_event_inbox_evaluation_run_id UNIQUE (evaluation_run_id),
            CONSTRAINT ck_event_inbox_producer CHECK (producer = 'B2'),
            CONSTRAINT ck_event_inbox_event_type CHECK (length(event_type) > 0)
        )
        """
    )
    op.execute("CREATE INDEX ix_event_inbox_occurred_at ON b3.event_inbox (occurred_at)")
    op.execute(
        "CREATE INDEX ix_event_inbox_question_version_id ON b3.event_inbox (question_version_id)"
    )
    op.execute(
        """
        CREATE TABLE b3.event_outbox (
            outbox_id uuid,
            CONSTRAINT pk_event_outbox PRIMARY KEY (outbox_id),
            event_id uuid NOT NULL,
            event_type varchar(200) NOT NULL,
            schema_version integer NOT NULL,
            occurred_at timestamptz(3) NOT NULL,
            correlation_id uuid NOT NULL,
            causation_id uuid,
            producer varchar(8) NOT NULL DEFAULT 'B3',
            payload jsonb NOT NULL,
            attempts integer NOT NULL DEFAULT 0,
            published_at timestamptz(3),
            last_error text,
            created_at timestamptz(3) NOT NULL,
            CONSTRAINT uq_event_outbox_event_id UNIQUE (event_id),
            CONSTRAINT ck_event_outbox_producer CHECK (producer = 'B3')
        )
        """
    )
    op.execute("CREATE INDEX ix_event_outbox_published_at ON b3.event_outbox (published_at)")


def _create_statistics() -> None:
    op.execute(
        """
        CREATE TABLE b3.question_version_stats (
            question_version_id uuid,
            CONSTRAINT pk_question_version_stats PRIMARY KEY (question_version_id),
            attempt_count bigint NOT NULL DEFAULT 0,
            pass_known_count bigint NOT NULL DEFAULT 0,
            pass_count bigint NOT NULL DEFAULT 0,
            unbucketed_attempt_count bigint NOT NULL DEFAULT 0,
            first_occurred_at timestamptz(3),
            last_occurred_at timestamptz(3),
            updated_at timestamptz(3) NOT NULL,
            CONSTRAINT ck_question_version_stats_attempt_count CHECK (attempt_count >= 0),
            CONSTRAINT ck_question_version_stats_pass_known CHECK (pass_known_count >= 0),
            CONSTRAINT ck_question_version_stats_pass_count CHECK (pass_count >= 0),
            CONSTRAINT ck_question_version_stats_pass_le_known CHECK (pass_count <= pass_known_count),
            CONSTRAINT ck_question_version_stats_unbucketed CHECK (unbucketed_attempt_count >= 0)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE b3.question_version_score_buckets (
            question_version_id uuid NOT NULL,
            bucket_label varchar(32) NOT NULL,
            bucket_count bigint NOT NULL DEFAULT 0,
            CONSTRAINT pk_question_version_score_buckets PRIMARY KEY (question_version_id, bucket_label),
            CONSTRAINT ck_question_version_score_buckets_count CHECK (bucket_count >= 0)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE b3.question_version_failure_categories (
            question_version_id uuid NOT NULL,
            failed_category varchar(64) NOT NULL,
            category_count bigint NOT NULL DEFAULT 0,
            CONSTRAINT pk_question_version_failure_categories PRIMARY KEY (question_version_id, failed_category),
            CONSTRAINT ck_question_version_failure_categories_count CHECK (category_count >= 0)
        )
        """
    )
    op.execute(
        "COMMENT ON TABLE b3.question_version_stats IS "
        "'De-identified counters derived from evaluation.completed events only'"
    )


# --------------------------------------------------------------------------
# immutability
# --------------------------------------------------------------------------


def _create_immutability_triggers() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION b3.tg_question_versions_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF OLD.status <> 'draft' THEN
                    RAISE EXCEPTION
                        'question version % is % and cannot be deleted', OLD.question_version_id, OLD.status
                        USING ERRCODE = '23001';
                END IF;
                RETURN OLD;
            END IF;

            IF OLD.status = 'archived' THEN
                RAISE EXCEPTION
                    'archived question version % is immutable', OLD.question_version_id
                    USING ERRCODE = '23001';
            END IF;

            IF OLD.status = 'published' THEN
                IF NEW.status NOT IN ('published', 'archived') THEN
                    RAISE EXCEPTION
                        'invalid status transition % -> % on question version %',
                        OLD.status, NEW.status, OLD.question_version_id
                        USING ERRCODE = '23001';
                END IF;
                IF NEW.question_id IS DISTINCT FROM OLD.question_id
                   OR NEW.version IS DISTINCT FROM OLD.version
                   OR NEW.language IS DISTINCT FROM OLD.language
                   OR NEW.stem_markdown IS DISTINCT FROM OLD.stem_markdown
                   OR NEW.adapter_manifest IS DISTINCT FROM OLD.adapter_manifest
                   OR NEW.content_digest IS DISTINCT FROM OLD.content_digest
                   OR NEW.package_digest IS DISTINCT FROM OLD.package_digest
                   OR NEW.max_files IS DISTINCT FROM OLD.max_files
                   OR NEW.max_total_bytes IS DISTINCT FROM OLD.max_total_bytes
                   OR NEW.published_at IS DISTINCT FROM OLD.published_at THEN
                    RAISE EXCEPTION
                        'published question version % is immutable; create a new version',
                        OLD.question_version_id
                        USING ERRCODE = '23001';
                END IF;
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_question_versions_guard
        BEFORE UPDATE OR DELETE ON b3.question_versions
        FOR EACH ROW EXECUTE FUNCTION b3.tg_question_versions_guard()
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION b3.tg_protected_content_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            owner_id uuid;
            owner_status text;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                owner_id := OLD.question_version_id;
            ELSE
                owner_id := NEW.question_version_id;
            END IF;
            SELECT status INTO owner_status
              FROM b3.question_versions
             WHERE question_version_id = owner_id;
            IF owner_status IS NOT NULL AND owner_status <> 'draft' THEN
                RAISE EXCEPTION
                    'content of % question version % is immutable',
                    owner_status, owner_id
                    USING ERRCODE = '23001';
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    for table in ("reference_answers", "test_suites", "test_cases", "fixtures"):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_guard
            BEFORE INSERT OR UPDATE OR DELETE ON b3.{table}
            FOR EACH ROW EXECUTE FUNCTION b3.tg_protected_content_guard()
            """
        )


# --------------------------------------------------------------------------
# privileges
# --------------------------------------------------------------------------


def _grant_privileges() -> None:
    """Least privilege for the two application roles, when they exist.

    The roles are created by ``infra/postgres/init.sql`` (local compose). A database
    provisioned by B4 may use different names; the grant block therefore checks for
    their existence and skips silently instead of failing the migration - the
    privilege contract is documented in ``docs/database/schema.md`` either way.
    """
    protected = ", ".join(f"b3.{table}" for table in PROTECTED_TABLES)
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'autograder_b3_app') THEN
                GRANT USAGE ON SCHEMA b3 TO autograder_b3_app;
                GRANT SELECT ON ALL TABLES IN SCHEMA b3 TO autograder_b3_app;
                REVOKE ALL ON {protected} FROM autograder_b3_app;
                GRANT INSERT, UPDATE, DELETE ON
                    b3.event_inbox,
                    b3.question_version_stats,
                    b3.question_version_score_buckets,
                    b3.question_version_failure_categories
                    TO autograder_b3_app;
                GRANT UPDATE ON b3.event_outbox TO autograder_b3_app;
            END IF;

            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'autograder_b3_internal') THEN
                GRANT USAGE ON SCHEMA b3 TO autograder_b3_internal;
                GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA b3
                    TO autograder_b3_internal;
            END IF;
        END
        $$
        """
    )
    # The app role must not be able to reach protected content through a view or a
    # future table created by the owner.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'autograder_b3_app') THEN
                ALTER DEFAULT PRIVILEGES IN SCHEMA b3
                    GRANT SELECT ON TABLES TO autograder_b3_app;
            END IF;
        END
        $$
        """
    )
