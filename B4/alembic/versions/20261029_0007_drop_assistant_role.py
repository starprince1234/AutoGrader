"""Drop the assistant role.

Revision ID: 20261029_0007
Revises: 20261029_0006
"""

from alembic import op

revision = "20261029_0007"
down_revision = "20261029_0006"
branch_labels = None
depends_on = None

SCHEMA = "b4"
CONSTRAINT = "ck_roles_role"
WITH_ASSISTANT = "'student', 'teacher', 'assistant', 'admin'"
WITHOUT_ASSISTANT = "'student', 'teacher', 'admin'"


def upgrade() -> None:
    # Remove the grants first: the constraint must not be widened-then-narrowed with rows that
    # violate the target state.
    op.execute(f"DELETE FROM {SCHEMA}.roles WHERE role = 'assistant'")
    op.execute(f"ALTER TABLE {SCHEMA}.roles DROP CONSTRAINT IF EXISTS {CONSTRAINT}")
    op.execute(
        f"ALTER TABLE {SCHEMA}.roles ADD CONSTRAINT {CONSTRAINT} "
        f"CHECK (role IN ({WITHOUT_ASSISTANT}))"
    )


def downgrade() -> None:
    op.execute(f"ALTER TABLE {SCHEMA}.roles DROP CONSTRAINT IF EXISTS {CONSTRAINT}")
    op.execute(
        f"ALTER TABLE {SCHEMA}.roles ADD CONSTRAINT {CONSTRAINT} "
        f"CHECK (role IN ({WITH_ASSISTANT}))"
    )
