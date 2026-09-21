from alembic import op

revision = "20260915_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Ensure the `b4` schema exists.

    The schema itself is created by the provisioning step (`python -m
    autograder_b4.ops.provision`), which is the only place allowed to hold
    instance-level privileges. This revision therefore creates the schema only when it
    is genuinely absent, and otherwise does nothing.

    The guard is not cosmetic: PostgreSQL checks `CREATE` on the *database* before it
    honours `IF NOT EXISTS`, so an unguarded `CREATE SCHEMA IF NOT EXISTS b4` fails for
    the least-privilege application role even when the schema already exists. Keeping
    the migration guarded lets `alembic upgrade head` run as the application role, which
    is what the runbook requires.
    """
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'b4') THEN
                CREATE SCHEMA b4;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    """Drop the schema. Requires the schema owner, i.e. a privileged operator."""
    op.execute("DROP SCHEMA IF EXISTS b4")
