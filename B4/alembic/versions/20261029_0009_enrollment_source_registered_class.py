"""Allow `registered_class` as an enrollment source.

Revision ID: 20261029_0009
Revises: 20261029_0007
"""

from alembic import op

revision = "20261029_0009"
down_revision = "20261029_0007"
branch_labels = None
depends_on = None

SCHEMA = "b4"
CONSTRAINT = "ck_enrollments_source"
WIDENED = "'roster_import', 'manual', 'registered_class'"
ORIGINAL = "'roster_import', 'manual'"


def upgrade() -> None:
    op.execute(f"ALTER TABLE {SCHEMA}.enrollments DROP CONSTRAINT IF EXISTS {CONSTRAINT}")
    op.execute(
        f"ALTER TABLE {SCHEMA}.enrollments ADD CONSTRAINT {CONSTRAINT} "
        f"CHECK (source IN ({WIDENED}))"
    )


def downgrade() -> None:
    # Rows of the new source fall back to manual, otherwise narrowing the check fails.
    op.execute(
        f"UPDATE {SCHEMA}.enrollments SET source = 'manual' WHERE source = 'registered_class'"
    )
    op.execute(f"ALTER TABLE {SCHEMA}.enrollments DROP CONSTRAINT IF EXISTS {CONSTRAINT}")
    op.execute(
        f"ALTER TABLE {SCHEMA}.enrollments ADD CONSTRAINT {CONSTRAINT} "
        f"CHECK (source IN ({ORIGINAL}))"
    )
