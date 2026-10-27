"""Password reset codes: a one-time mailbox proof for replacing a password.

Revision ID: 20261027_0005
Revises: 20261027_0004

Why a separate table instead of reusing `registration_codes`: registration codes are bound
to an *intent to create* an account (username, student number, display name), while a reset
code is bound to an existing `user_id` and the mailbox on that account. Storing both in one
table would require nullable columns whose meaning depends on a second discriminator, which
is exactly the kind of state a reader can misread. The two rows also have different
lifecycles: registration consumes the row by creating the account, reset consumes it by
replacing the credential and revoking every refresh session.

Only a SHA-256 digest bound to `code_id` is stored, mirroring `registration_codes`; the
plaintext code never reaches the database, a log line or an audit record.
"""

from alembic import op

revision = "20261027_0005"
down_revision = "20261027_0004"
branch_labels = None
depends_on = None

SCHEMA = "b4"

TIMESTAMPTZ = "timestamptz(3)"
NOW = "date_trunc('milliseconds', now())"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE {SCHEMA}.password_reset_codes (
            code_id     uuid        NOT NULL,
            user_id     uuid        NOT NULL,
            email       text        NOT NULL,
            code_hash   text        NOT NULL,
            attempts    integer     NOT NULL DEFAULT 0,
            expires_at  {TIMESTAMPTZ} NOT NULL,
            consumed_at {TIMESTAMPTZ} NULL,
            created_at  {TIMESTAMPTZ} NOT NULL DEFAULT {NOW},
            CONSTRAINT pk_password_reset_codes PRIMARY KEY (code_id),
            CONSTRAINT fk_password_reset_codes_user_id
                FOREIGN KEY (user_id) REFERENCES {SCHEMA}.users (user_id) ON DELETE CASCADE,
            CONSTRAINT ck_password_reset_codes_hash CHECK (code_hash ~ '^[0-9a-f]{{64}}$'),
            CONSTRAINT ck_password_reset_codes_email_is_normalized
                CHECK (email = lower(btrim(email))),
            CONSTRAINT ck_password_reset_codes_attempts CHECK (attempts >= 0)
        )
        """
    )
    op.execute(
        f"CREATE INDEX ix_password_reset_codes_user_id "
        f"ON {SCHEMA}.password_reset_codes (user_id)"
    )
    op.execute(
        f"CREATE INDEX ix_password_reset_codes_email_created_at "
        f"ON {SCHEMA}.password_reset_codes (email, created_at DESC)"
    )
    op.execute(
        f"CREATE INDEX ix_password_reset_codes_created_at "
        f"ON {SCHEMA}.password_reset_codes (created_at)"
    )
    _grant_application_role()


def downgrade() -> None:
    op.execute(f'DROP TABLE IF EXISTS {SCHEMA}."password_reset_codes"')


def _grant_application_role() -> None:
    """Grant on the new table to the provisioned application role, when it exists.

    Same shape as the 0003 helper: a fresh database that has not been provisioned yet must
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
