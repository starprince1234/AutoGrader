"""Create the B3-owned schema baseline."""

from alembic import op

revision = "20260915_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS b3")


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS b3")
