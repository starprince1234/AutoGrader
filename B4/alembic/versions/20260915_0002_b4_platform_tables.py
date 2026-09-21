"""Create the B4 platform tables.

Self-contained by design: the DDL is literal here so that replaying this revision on an
empty database a year from now produces exactly today's schema. The application's
SQLAlchemy table definitions in `autograder_b4.db.tables` are checked *against the live
database* by `tests/test_schema_conformance.py`, which detects drift in either
direction without coupling a historical migration to current application code.

Conventions follow `docs/database/table-standards.md`: plural snake_case tables, UUID v4
primary keys named `<entity>_id`, `ix_`/`uq_`/`fk_` prefixes, `timestamptz` at
millisecond precision, and foreign keys restricted to this schema.

Revision ID: 20260915_0002
Revises: 20260915_0001
"""

from alembic import op

revision = "20260915_0002"
down_revision = "20260915_0001"
branch_labels = None
depends_on = None

SCHEMA = "b4"

#: `timestamptz` truncated to milliseconds, so stored and wire formats agree.
TIMESTAMPTZ = "timestamptz(3)"
NOW = "date_trunc('milliseconds', now())"

TABLES = (
    "users",
    "courses",
    "sections",
    "roles",
    "credentials",
    "sessions",
    "enrollments",
    "roster_imports",
    "roster_import_files",
    "roster_import_rows",
    "audit_records",
    "outbox_events",
    "inbox_events",
    "dlq_messages",
    "login_attempts",
)

ROLE_VALUES = "'student', 'teacher', 'assistant', 'admin'"


def upgrade() -> None:
    _create_users()
    _create_courses()
    _create_sections()
    _create_roles()
    _create_credentials()
    _create_sessions()
    _create_roster_imports()
    _create_roster_import_files()
    _create_roster_import_rows()
    _create_enrollments()
    _create_audit_records()
    _create_outbox_events()
    _create_inbox_events()
    _create_dlq_messages()
    _create_login_attempts()
    _grant_application_role()


def downgrade() -> None:
    # Dropped in reverse dependency order; RESTRICT foreign keys make an out-of-order
    # drop fail loudly instead of silently cascading.
    for table in reversed(TABLES):
        op.execute(f'DROP TABLE IF EXISTS {SCHEMA}."{table}"')


def _create_users() -> None:
    op.execute(
        f"""
        CREATE TABLE {SCHEMA}.users (
            user_id         uuid        NOT NULL,
            username        text        NOT NULL,
            display_name    text        NOT NULL,
            student_number  text        NULL,
            email           text        NULL,
            is_active       boolean     NOT NULL DEFAULT true,
            deactivated_at  {TIMESTAMPTZ} NULL,
            created_at      {TIMESTAMPTZ} NOT NULL DEFAULT {NOW},
            updated_at      {TIMESTAMPTZ} NOT NULL DEFAULT {NOW},
            CONSTRAINT pk_users PRIMARY KEY (user_id),
            CONSTRAINT uq_users_username UNIQUE (username),
            CONSTRAINT uq_users_student_number UNIQUE (student_number),
            CONSTRAINT ck_users_username_not_blank CHECK (length(btrim(username)) > 0),
            CONSTRAINT ck_users_display_name_not_blank CHECK (length(btrim(display_name)) > 0)
        )
        """
    )
    op.execute(f"CREATE INDEX ix_users_created_at ON {SCHEMA}.users (created_at)")
    op.execute(f"CREATE INDEX ix_users_is_active ON {SCHEMA}.users (is_active) WHERE is_active")


def _create_courses() -> None:
    op.execute(
        f"""
        CREATE TABLE {SCHEMA}.courses (
            course_id    uuid        NOT NULL,
            course_code  text        NOT NULL,
            title        text        NOT NULL,
            created_by   uuid        NULL,
            is_active    boolean     NOT NULL DEFAULT true,
            created_at   {TIMESTAMPTZ} NOT NULL DEFAULT {NOW},
            updated_at   {TIMESTAMPTZ} NOT NULL DEFAULT {NOW},
            CONSTRAINT pk_courses PRIMARY KEY (course_id),
            CONSTRAINT uq_courses_course_code UNIQUE (course_code),
            CONSTRAINT fk_courses_created_by FOREIGN KEY (created_by)
                REFERENCES {SCHEMA}.users (user_id) ON DELETE SET NULL,
            CONSTRAINT ck_courses_course_code_not_blank CHECK (length(btrim(course_code)) > 0),
            CONSTRAINT ck_courses_title_not_blank CHECK (length(btrim(title)) > 0)
        )
        """
    )
    op.execute(f"CREATE INDEX ix_courses_created_at ON {SCHEMA}.courses (created_at)")


def _create_sections() -> None:
    op.execute(
        f"""
        CREATE TABLE {SCHEMA}.sections (
            section_id    uuid        NOT NULL,
            course_id     uuid        NOT NULL,
            section_code  text        NOT NULL,
            term          text        NULL,
            created_by    uuid        NULL,
            created_at    {TIMESTAMPTZ} NOT NULL DEFAULT {NOW},
            updated_at    {TIMESTAMPTZ} NOT NULL DEFAULT {NOW},
            CONSTRAINT pk_sections PRIMARY KEY (section_id),
            CONSTRAINT fk_sections_course_id FOREIGN KEY (course_id)
                REFERENCES {SCHEMA}.courses (course_id) ON DELETE RESTRICT,
            CONSTRAINT fk_sections_created_by FOREIGN KEY (created_by)
                REFERENCES {SCHEMA}.users (user_id) ON DELETE SET NULL,
            CONSTRAINT ck_sections_section_code_not_blank CHECK (length(btrim(section_code)) > 0)
        )
        """
    )
    # `term` is nullable, so the natural key needs NULLS NOT DISTINCT to stay unique.
    op.execute(
        f"""
        CREATE UNIQUE INDEX uq_sections_course_id_section_code_term
            ON {SCHEMA}.sections (course_id, section_code, term) NULLS NOT DISTINCT
        """
    )
    op.execute(f"CREATE INDEX ix_sections_course_id ON {SCHEMA}.sections (course_id)")


def _create_roles() -> None:
    op.execute(
        f"""
        CREATE TABLE {SCHEMA}.roles (
            role_id     uuid        NOT NULL,
            user_id     uuid        NOT NULL,
            role        text        NOT NULL,
            course_id   uuid        NULL,
            granted_by  uuid        NULL,
            created_at  {TIMESTAMPTZ} NOT NULL DEFAULT {NOW},
            CONSTRAINT pk_roles PRIMARY KEY (role_id),
            CONSTRAINT fk_roles_user_id FOREIGN KEY (user_id)
                REFERENCES {SCHEMA}.users (user_id) ON DELETE CASCADE,
            CONSTRAINT fk_roles_course_id FOREIGN KEY (course_id)
                REFERENCES {SCHEMA}.courses (course_id) ON DELETE CASCADE,
            CONSTRAINT fk_roles_granted_by FOREIGN KEY (granted_by)
                REFERENCES {SCHEMA}.users (user_id) ON DELETE SET NULL,
            CONSTRAINT ck_roles_role CHECK (role IN ({ROLE_VALUES})),
            CONSTRAINT ck_roles_admin_is_platform_wide CHECK (role <> 'admin' OR course_id IS NULL)
        )
        """
    )
    op.execute(
        f"""
        CREATE UNIQUE INDEX uq_roles_user_id_role_course_id
            ON {SCHEMA}.roles (user_id, role, course_id) NULLS NOT DISTINCT
        """
    )
    op.execute(f"CREATE INDEX ix_roles_user_id ON {SCHEMA}.roles (user_id)")
    op.execute(f"CREATE INDEX ix_roles_course_id ON {SCHEMA}.roles (course_id)")


def _create_credentials() -> None:
    op.execute(
        f"""
        CREATE TABLE {SCHEMA}.credentials (
            credential_id   uuid        NOT NULL,
            user_id         uuid        NOT NULL,
            password_hash   text        NOT NULL,
            hash_algorithm  text        NOT NULL,
            created_at      {TIMESTAMPTZ} NOT NULL DEFAULT {NOW},
            updated_at      {TIMESTAMPTZ} NOT NULL DEFAULT {NOW},
            CONSTRAINT pk_credentials PRIMARY KEY (credential_id),
            CONSTRAINT uq_credentials_user_id UNIQUE (user_id),
            CONSTRAINT fk_credentials_user_id FOREIGN KEY (user_id)
                REFERENCES {SCHEMA}.users (user_id) ON DELETE CASCADE,
            CONSTRAINT ck_credentials_hash_algorithm
                CHECK (hash_algorithm IN ('argon2id', 'bcrypt'))
        )
        """
    )


def _create_sessions() -> None:
    op.execute(
        f"""
        CREATE TABLE {SCHEMA}.sessions (
            session_id          uuid        NOT NULL,
            user_id             uuid        NOT NULL,
            family_id           uuid        NOT NULL,
            refresh_token_hash  text        NOT NULL,
            issued_at           {TIMESTAMPTZ} NOT NULL DEFAULT {NOW},
            expires_at          {TIMESTAMPTZ} NOT NULL,
            rotated_at          {TIMESTAMPTZ} NULL,
            revoked_at          {TIMESTAMPTZ} NULL,
            replaced_by         uuid        NULL,
            revoked_reason      text        NULL,
            created_at          {TIMESTAMPTZ} NOT NULL DEFAULT {NOW},
            updated_at          {TIMESTAMPTZ} NOT NULL DEFAULT {NOW},
            CONSTRAINT pk_sessions PRIMARY KEY (session_id),
            CONSTRAINT fk_sessions_user_id FOREIGN KEY (user_id)
                REFERENCES {SCHEMA}.users (user_id) ON DELETE CASCADE,
            CONSTRAINT fk_sessions_replaced_by FOREIGN KEY (replaced_by)
                REFERENCES {SCHEMA}.sessions (session_id) ON DELETE SET NULL,
            CONSTRAINT uq_sessions_refresh_token_hash UNIQUE (refresh_token_hash),
            CONSTRAINT ck_sessions_refresh_hash_is_sha256
                CHECK (refresh_token_hash ~ '^[0-9a-f]{{64}}$')
        )
        """
    )
    op.execute(f"CREATE INDEX ix_sessions_user_id ON {SCHEMA}.sessions (user_id)")
    op.execute(f"CREATE INDEX ix_sessions_family_id ON {SCHEMA}.sessions (family_id)")
    op.execute(f"CREATE INDEX ix_sessions_expires_at ON {SCHEMA}.sessions (expires_at)")


def _create_roster_imports() -> None:
    op.execute(
        f"""
        CREATE TABLE {SCHEMA}.roster_imports (
            roster_import_id    uuid        NOT NULL,
            course_id           uuid        NOT NULL,
            section_id          uuid        NOT NULL,
            status              text        NOT NULL,
            filename            text        NOT NULL,
            media_type          text        NOT NULL,
            content_sha256      text        NOT NULL,
            size_bytes          bigint      NOT NULL,
            detected_columns    jsonb       NOT NULL DEFAULT '[]'::jsonb,
            proposed_mapping    jsonb       NOT NULL DEFAULT '{{}}'::jsonb,
            confirmed_mapping   jsonb       NULL,
            parse_detail        text        NULL,
            accepted_count      integer     NOT NULL DEFAULT 0,
            rejected_count      integer     NOT NULL DEFAULT 0,
            idempotency_key     text        NULL,
            created_by          uuid        NULL,
            created_at          {TIMESTAMPTZ} NOT NULL DEFAULT {NOW},
            updated_at          {TIMESTAMPTZ} NOT NULL DEFAULT {NOW},
            committed_at        {TIMESTAMPTZ} NULL,
            CONSTRAINT pk_roster_imports PRIMARY KEY (roster_import_id),
            CONSTRAINT fk_roster_imports_course_id FOREIGN KEY (course_id)
                REFERENCES {SCHEMA}.courses (course_id) ON DELETE RESTRICT,
            CONSTRAINT fk_roster_imports_section_id FOREIGN KEY (section_id)
                REFERENCES {SCHEMA}.sections (section_id) ON DELETE RESTRICT,
            CONSTRAINT fk_roster_imports_created_by FOREIGN KEY (created_by)
                REFERENCES {SCHEMA}.users (user_id) ON DELETE SET NULL,
            CONSTRAINT ck_roster_imports_status CHECK (status IN (
                'uploaded', 'parsed', 'mapping_required', 'validating',
                'previewed', 'committed', 'rejected', 'failed_parse')),
            CONSTRAINT ck_roster_imports_media_type CHECK (media_type IN (
                'application/pdf',
                'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')),
            CONSTRAINT ck_roster_imports_content_sha256 CHECK (content_sha256 ~ '^[0-9a-f]{{64}}$'),
            CONSTRAINT ck_roster_imports_size_bytes CHECK (size_bytes >= 0),
            CONSTRAINT ck_roster_imports_counts CHECK (accepted_count >= 0 AND rejected_count >= 0),
            CONSTRAINT ck_roster_imports_committed_consistency
                CHECK ((status = 'committed') = (committed_at IS NOT NULL))
        )
        """
    )
    op.execute(
        f"""
        CREATE UNIQUE INDEX uq_roster_imports_idempotency_key
            ON {SCHEMA}.roster_imports (idempotency_key) WHERE idempotency_key IS NOT NULL
        """
    )
    op.execute(f"CREATE INDEX ix_roster_imports_section_id ON {SCHEMA}.roster_imports (section_id)")
    op.execute(f"CREATE INDEX ix_roster_imports_created_at ON {SCHEMA}.roster_imports (created_at)")
    op.execute(
        f"CREATE INDEX ix_roster_imports_status ON {SCHEMA}.roster_imports (status) "
        "WHERE status <> 'committed'"
    )


def _create_roster_import_files() -> None:
    op.execute(
        f"""
        CREATE TABLE {SCHEMA}.roster_import_files (
            file_id             uuid        NOT NULL,
            roster_import_id    uuid        NOT NULL,
            content             bytea       NOT NULL,
            created_at          {TIMESTAMPTZ} NOT NULL DEFAULT {NOW},
            CONSTRAINT pk_roster_import_files PRIMARY KEY (file_id),
            CONSTRAINT uq_roster_import_files_roster_import_id UNIQUE (roster_import_id),
            CONSTRAINT fk_roster_import_files_roster_import_id FOREIGN KEY (roster_import_id)
                REFERENCES {SCHEMA}.roster_imports (roster_import_id) ON DELETE CASCADE
        )
        """
    )


def _create_roster_import_rows() -> None:
    op.execute(
        f"""
        CREATE TABLE {SCHEMA}.roster_import_rows (
            row_id              uuid        NOT NULL,
            roster_import_id    uuid        NOT NULL,
            row_number          integer     NOT NULL,
            status              text        NOT NULL,
            row_values          jsonb       NOT NULL,
            reason              text        NULL,
            created_at          {TIMESTAMPTZ} NOT NULL DEFAULT {NOW},
            CONSTRAINT pk_roster_import_rows PRIMARY KEY (row_id),
            CONSTRAINT fk_roster_import_rows_roster_import_id FOREIGN KEY (roster_import_id)
                REFERENCES {SCHEMA}.roster_imports (roster_import_id) ON DELETE CASCADE,
            CONSTRAINT ck_roster_import_rows_status CHECK (status IN ('accepted', 'rejected')),
            CONSTRAINT ck_roster_import_rows_row_number CHECK (row_number >= 1),
            CONSTRAINT ck_roster_import_rows_reason CHECK (
                (status = 'rejected') = (reason IS NOT NULL))
        )
        """
    )
    op.execute(
        f"""
        CREATE UNIQUE INDEX uq_roster_import_rows_roster_import_id_row_number
            ON {SCHEMA}.roster_import_rows (roster_import_id, row_number)
        """
    )


def _create_enrollments() -> None:
    op.execute(
        f"""
        CREATE TABLE {SCHEMA}.enrollments (
            enrollment_id       uuid        NOT NULL,
            section_id          uuid        NOT NULL,
            user_id             uuid        NULL,
            student_number      text        NOT NULL,
            display_name        text        NOT NULL,
            source              text        NOT NULL,
            roster_import_id    uuid        NULL,
            is_active           boolean     NOT NULL DEFAULT true,
            created_at          {TIMESTAMPTZ} NOT NULL DEFAULT {NOW},
            updated_at          {TIMESTAMPTZ} NOT NULL DEFAULT {NOW},
            CONSTRAINT pk_enrollments PRIMARY KEY (enrollment_id),
            CONSTRAINT fk_enrollments_section_id FOREIGN KEY (section_id)
                REFERENCES {SCHEMA}.sections (section_id) ON DELETE CASCADE,
            CONSTRAINT fk_enrollments_user_id FOREIGN KEY (user_id)
                REFERENCES {SCHEMA}.users (user_id) ON DELETE SET NULL,
            CONSTRAINT fk_enrollments_roster_import_id FOREIGN KEY (roster_import_id)
                REFERENCES {SCHEMA}.roster_imports (roster_import_id) ON DELETE SET NULL,
            CONSTRAINT ck_enrollments_source CHECK (source IN ('roster_import', 'manual')),
            CONSTRAINT ck_enrollments_student_number_not_blank
                CHECK (length(btrim(student_number)) > 0),
            CONSTRAINT ck_enrollments_display_name_not_blank
                CHECK (length(btrim(display_name)) > 0)
        )
        """
    )
    op.execute(
        f"""
        CREATE UNIQUE INDEX uq_enrollments_section_id_student_number
            ON {SCHEMA}.enrollments (section_id, student_number)
        """
    )
    op.execute(f"CREATE INDEX ix_enrollments_section_id ON {SCHEMA}.enrollments (section_id)")
    op.execute(f"CREATE INDEX ix_enrollments_user_id ON {SCHEMA}.enrollments (user_id)")
    op.execute(
        f"CREATE INDEX ix_enrollments_roster_import_id ON {SCHEMA}.enrollments (roster_import_id)"
    )


def _create_audit_records() -> None:
    op.execute(
        f"""
        CREATE TABLE {SCHEMA}.audit_records (
            audit_id        uuid        NOT NULL,
            occurred_at     {TIMESTAMPTZ} NOT NULL DEFAULT {NOW},
            actor_user_id   uuid        NULL,
            actor_roles     text[]      NOT NULL DEFAULT ARRAY[]::text[],
            action          text        NOT NULL,
            outcome         text        NOT NULL,
            target_type     text        NULL,
            target_id       uuid        NULL,
            course_id       uuid        NULL,
            section_id      uuid        NULL,
            request_id      text        NULL,
            correlation_id  text        NULL,
            change_digest   text        NULL,
            detail          text        NULL,
            CONSTRAINT pk_audit_records PRIMARY KEY (audit_id),
            CONSTRAINT fk_audit_records_actor_user_id FOREIGN KEY (actor_user_id)
                REFERENCES {SCHEMA}.users (user_id) ON DELETE SET NULL,
            CONSTRAINT fk_audit_records_course_id FOREIGN KEY (course_id)
                REFERENCES {SCHEMA}.courses (course_id) ON DELETE SET NULL,
            CONSTRAINT fk_audit_records_section_id FOREIGN KEY (section_id)
                REFERENCES {SCHEMA}.sections (section_id) ON DELETE SET NULL,
            CONSTRAINT ck_audit_records_outcome CHECK (outcome IN ('success', 'denied', 'failure')),
            CONSTRAINT ck_audit_records_change_digest
                CHECK (change_digest IS NULL OR change_digest ~ '^[0-9a-f]{{64}}$')
        )
        """
    )
    op.execute(f"CREATE INDEX ix_audit_records_occurred_at ON {SCHEMA}.audit_records (occurred_at)")
    op.execute(
        f"CREATE INDEX ix_audit_records_actor_user_id ON {SCHEMA}.audit_records (actor_user_id)"
    )
    op.execute(f"CREATE INDEX ix_audit_records_course_id ON {SCHEMA}.audit_records (course_id)")
    op.execute(f"CREATE INDEX ix_audit_records_action ON {SCHEMA}.audit_records (action)")


def _create_outbox_events() -> None:
    op.execute(
        f"""
        CREATE TABLE {SCHEMA}.outbox_events (
            outbox_id       uuid        NOT NULL,
            event_id        uuid        NOT NULL,
            event_type      text        NOT NULL,
            schema_version  integer     NOT NULL,
            occurred_at     {TIMESTAMPTZ} NOT NULL,
            correlation_id  uuid        NOT NULL,
            causation_id    uuid        NULL,
            producer        text        NOT NULL,
            payload         jsonb       NOT NULL,
            published_at    {TIMESTAMPTZ} NULL,
            attempts        integer     NOT NULL DEFAULT 0,
            last_error      text        NULL,
            created_at      {TIMESTAMPTZ} NOT NULL DEFAULT {NOW},
            CONSTRAINT pk_outbox_events PRIMARY KEY (outbox_id),
            CONSTRAINT uq_outbox_events_event_id UNIQUE (event_id),
            CONSTRAINT ck_outbox_events_event_type CHECK (event_type ~ '^autograder\\.v1\\.'),
            CONSTRAINT ck_outbox_events_producer CHECK (producer ~ '^B[1-4]$'),
            CONSTRAINT ck_outbox_events_schema_version CHECK (schema_version >= 1),
            CONSTRAINT ck_outbox_events_attempts CHECK (attempts >= 0)
        )
        """
    )
    op.execute(
        f"""
        CREATE INDEX ix_outbox_events_published_at
            ON {SCHEMA}.outbox_events (created_at) WHERE published_at IS NULL
        """
    )


def _create_inbox_events() -> None:
    op.execute(
        f"""
        CREATE TABLE {SCHEMA}.inbox_events (
            inbox_id    uuid        NOT NULL,
            consumer    text        NOT NULL,
            event_id    uuid        NOT NULL,
            subject     text        NOT NULL,
            received_at {TIMESTAMPTZ} NOT NULL DEFAULT {NOW},
            CONSTRAINT pk_inbox_events PRIMARY KEY (inbox_id),
            CONSTRAINT ck_inbox_events_consumer_not_blank CHECK (length(btrim(consumer)) > 0)
        )
        """
    )
    # The deduplication guarantee for at-least-once delivery: a second delivery of the
    # same event_id hits this unique key instead of producing a second business effect.
    op.execute(
        f"""
        CREATE UNIQUE INDEX uq_inbox_events_consumer_event_id
            ON {SCHEMA}.inbox_events (consumer, event_id)
        """
    )


def _create_dlq_messages() -> None:
    op.execute(
        f"""
        CREATE TABLE {SCHEMA}.dlq_messages (
            dlq_id              uuid        NOT NULL,
            original_stream     text        NOT NULL,
            original_consumer   text        NULL,
            original_subject    text        NOT NULL,
            original_sequence   bigint      NULL,
            event_id            uuid        NULL,
            reason              text        NOT NULL,
            delivery_count      integer     NOT NULL DEFAULT 0,
            envelope            jsonb       NULL,
            created_at          {TIMESTAMPTZ} NOT NULL DEFAULT {NOW},
            replayed_at         {TIMESTAMPTZ} NULL,
            replay_attempts     integer     NOT NULL DEFAULT 0,
            CONSTRAINT pk_dlq_messages PRIMARY KEY (dlq_id),
            CONSTRAINT ck_dlq_messages_delivery_count CHECK (delivery_count >= 0),
            CONSTRAINT ck_dlq_messages_replay_attempts CHECK (replay_attempts >= 0),
            CONSTRAINT ck_dlq_messages_reason_not_blank CHECK (length(btrim(reason)) > 0)
        )
        """
    )
    op.execute(
        f"""
        CREATE UNIQUE INDEX uq_dlq_messages_original_stream_original_sequence
            ON {SCHEMA}.dlq_messages (original_stream, original_sequence) NULLS NOT DISTINCT
        """
    )
    op.execute(f"CREATE INDEX ix_dlq_messages_created_at ON {SCHEMA}.dlq_messages (created_at)")
    op.execute(
        f"CREATE INDEX ix_dlq_messages_replayed_at ON {SCHEMA}.dlq_messages (replayed_at) "
        "WHERE replayed_at IS NULL"
    )


def _create_login_attempts() -> None:
    op.execute(
        f"""
        CREATE TABLE {SCHEMA}.login_attempts (
            attempt_id      uuid        NOT NULL,
            principal_key   text        NOT NULL,
            client_key      text        NULL,
            succeeded       boolean     NOT NULL,
            occurred_at     {TIMESTAMPTZ} NOT NULL DEFAULT {NOW},
            CONSTRAINT pk_login_attempts PRIMARY KEY (attempt_id),
            CONSTRAINT ck_login_attempts_principal_key_sha256
                CHECK (principal_key ~ '^[0-9a-f]{{64}}$'),
            CONSTRAINT ck_login_attempts_client_key_sha256
                CHECK (client_key IS NULL OR client_key ~ '^[0-9a-f]{{64}}$')
        )
        """
    )
    op.execute(
        f"""
        CREATE INDEX ix_login_attempts_principal_key_occurred_at
            ON {SCHEMA}.login_attempts (principal_key, occurred_at)
        """
    )


def _grant_application_role() -> None:
    """Grant on the new tables to the provisioned application role, when it exists.

    A fresh database that has not been provisioned yet must still migrate, so the role
    is looked up rather than assumed. The grant is the same one the provisioning script
    applies to existing tables; this keeps the two paths in agreement.
    """
    op.execute(
        f"""
        DO $$
        DECLARE
            app_role text := current_setting('b4.application_role', true);
        BEGIN
            IF app_role IS NULL OR app_role = '' THEN
                RETURN;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = app_role) THEN
                RETURN;
            END IF;
            EXECUTE format('GRANT USAGE, CREATE ON SCHEMA {SCHEMA} TO %I', app_role);
            EXECUTE format(
                'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {SCHEMA} TO %I',
                app_role);
            EXECUTE format(
                'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {SCHEMA} TO %I', app_role);
            EXECUTE format(
                'ALTER DEFAULT PRIVILEGES IN SCHEMA {SCHEMA} '
                'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %I', app_role);
        END
        $$;
        """
    )
