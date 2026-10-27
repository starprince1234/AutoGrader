"""Self-service registration: allowlist, verification codes and the email uniqueness rule.

Revision ID: 20261020_0003
Revises: 20260915_0002

Three things land here, and each of them closes a hole that a "register and log in with no
approval" flow would otherwise open:

* `registration_allowlist` -- the identity anchor. Without it, anyone who knows a classmate's
  name and 13-digit student number can create an account carrying that classmate's identity;
  the roster already holds both fields, so the check is data we own rather than a new
  external service.
* `registration_codes` -- proof that the mailbox belongs to the claimant. Only a SHA-256
  digest is stored, bound to the exact intent (username/student number/name), so a leaked
  table yields no usable code and a code cannot be replayed against a different identity.
* the `lower(btrim(email))` unique index -- "the e-mail is bound to exactly one account".
  Registration is the first path that writes e-mail as an identity attribute, so the
  constraint has to move from convention into the database.

The index is created **after** an explicit duplicate check: silently keeping one of two
rows that share a mailbox would decide on the operator's behalf which account owns it.
"""

from sqlalchemy import text

from alembic import op

revision = "20261020_0003"
down_revision = "20260915_0002"
branch_labels = None
depends_on = None

SCHEMA = "b4"

TIMESTAMPTZ = "timestamptz(3)"
NOW = "date_trunc('milliseconds', now())"

NEW_TABLES = (
    "registration_allowlist",
    "registration_allowlist_batches",
    "registration_codes",
)


def upgrade() -> None:
    _add_created_via()
    _create_email_unique_index()
    _create_allowlist_batches()
    _create_allowlist()
    _create_registration_codes()
    _grant_application_role()


def downgrade() -> None:
    for table in NEW_TABLES:
        op.execute(f'DROP TABLE IF EXISTS {SCHEMA}."{table}"')
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.uq_users_email_normalized")
    op.execute(f"ALTER TABLE {SCHEMA}.users DROP CONSTRAINT IF EXISTS ck_users_created_via")
    op.execute(f"ALTER TABLE {SCHEMA}.users DROP COLUMN IF EXISTS created_via")


def _add_created_via() -> None:
    op.execute(
        f"ALTER TABLE {SCHEMA}.users "
        "ADD COLUMN IF NOT EXISTS created_via text NOT NULL DEFAULT 'admin'"
    )
    op.execute(
        f"""
        ALTER TABLE {SCHEMA}.users
            ADD CONSTRAINT ck_users_created_via
            CHECK (created_via IN ('admin', 'self'))
        """
    )


def _create_email_unique_index() -> None:
    """Fail loudly on duplicate mailboxes instead of choosing a winner."""
    duplicates = op.get_bind().execute(
        text(
            f"""
            SELECT count(*) FROM (
                SELECT lower(btrim(email)) AS normalized
                FROM {SCHEMA}.users
                WHERE email IS NOT NULL AND btrim(email) <> ''
                GROUP BY 1 HAVING count(*) > 1
            ) AS duplicates
            """
        )
    ).scalar_one()
    if duplicates:
        raise RuntimeError(
            f"refusing to create uq_users_email_normalized: {duplicates} normalized e-mail "
            "value(s) are shared by more than one row in b4.users. Resolve them (the audit trail "
            "in b4.audit_records names the actors) and re-run the migration; this revision will "
            "not pick one of the duplicates for you."
        )
    op.execute(
        f"""
        CREATE UNIQUE INDEX uq_users_email_normalized
            ON {SCHEMA}.users (lower(btrim(email)))
            WHERE email IS NOT NULL AND btrim(email) <> ''
        """
    )


def _create_allowlist_batches() -> None:
    op.execute(
        f"""
        CREATE TABLE {SCHEMA}.registration_allowlist_batches (
            batch_id        uuid        NOT NULL,
            filename        text        NOT NULL,
            content_sha256  text        NOT NULL,
            row_count       integer     NOT NULL,
            uploaded_by     uuid        NULL,
            created_at      {TIMESTAMPTZ} NOT NULL DEFAULT {NOW},
            CONSTRAINT pk_registration_allowlist_batches PRIMARY KEY (batch_id),
            CONSTRAINT fk_registration_allowlist_batches_uploaded_by
                FOREIGN KEY (uploaded_by) REFERENCES {SCHEMA}.users (user_id) ON DELETE SET NULL,
            CONSTRAINT ck_registration_allowlist_batches_sha256
                CHECK (content_sha256 ~ '^[0-9a-f]{{64}}$'),
            CONSTRAINT ck_registration_allowlist_batches_row_count
                CHECK (row_count >= 0),
            CONSTRAINT ck_registration_allowlist_batches_filename_not_blank
                CHECK (length(btrim(filename)) > 0)
        )
        """
    )


def _create_allowlist() -> None:
    op.execute(
        f"""
        CREATE TABLE {SCHEMA}.registration_allowlist (
            entry_id        uuid        NOT NULL,
            batch_id        uuid        NOT NULL,
            student_number  text        NOT NULL,
            display_name    text        NOT NULL,
            created_at      {TIMESTAMPTZ} NOT NULL DEFAULT {NOW},
            CONSTRAINT pk_registration_allowlist PRIMARY KEY (entry_id),
            CONSTRAINT fk_registration_allowlist_batch
                FOREIGN KEY (batch_id)
                REFERENCES {SCHEMA}.registration_allowlist_batches (batch_id) ON DELETE CASCADE,
            CONSTRAINT uq_registration_allowlist_student_number UNIQUE (student_number),
            CONSTRAINT ck_registration_allowlist_student_number_13_digits
                CHECK (student_number ~ '^[0-9]{{13}}$'),
            CONSTRAINT ck_registration_allowlist_display_name_not_blank
                CHECK (length(btrim(display_name)) > 0)
        )
        """
    )
    op.execute(
        f"CREATE INDEX ix_registration_allowlist_batch_id "
        f"ON {SCHEMA}.registration_allowlist (batch_id)"
    )


def _create_registration_codes() -> None:
    op.execute(
        f"""
        CREATE TABLE {SCHEMA}.registration_codes (
            code_id         uuid        NOT NULL,
            email           text        NOT NULL,
            username        text        NOT NULL,
            student_number  text        NOT NULL,
            display_name    text        NOT NULL,
            code_hash       text        NOT NULL,
            attempts        integer     NOT NULL DEFAULT 0,
            expires_at      {TIMESTAMPTZ} NOT NULL,
            consumed_at     {TIMESTAMPTZ} NULL,
            created_at      {TIMESTAMPTZ} NOT NULL DEFAULT {NOW},
            CONSTRAINT pk_registration_codes PRIMARY KEY (code_id),
            CONSTRAINT ck_registration_codes_hash CHECK (code_hash ~ '^[0-9a-f]{{64}}$'),
            CONSTRAINT ck_registration_codes_email_is_normalized
                CHECK (email = lower(btrim(email))),
            CONSTRAINT ck_registration_codes_attempts CHECK (attempts >= 0)
        )
        """
    )
    op.execute(
        f"CREATE INDEX ix_registration_codes_email_created_at "
        f"ON {SCHEMA}.registration_codes (email, created_at DESC)"
    )
    op.execute(
        f"CREATE INDEX ix_registration_codes_created_at "
        f"ON {SCHEMA}.registration_codes (created_at)"
    )


def _grant_application_role() -> None:
    """Grant on the new tables to the provisioned application role, when it exists.

    Same shape as the 0002 helper: a fresh database that has not been provisioned yet must
    still migrate, so the role is looked up rather than assumed.
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
            EXECUTE format(
                'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {SCHEMA} TO %I',
                app_role);
        END
        $$;
        """
    )
