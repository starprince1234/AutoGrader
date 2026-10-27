"""Roster import accepts CSV: widen the ``media_type`` CHECK constraint.

Revision ID: 20261027_0004
Revises: 20261020_0003

Why a migration instead of only a parser change: `roster_imports.media_type` carried
``CHECK (media_type IN ('application/pdf', <xlsx>))`` from the original platform migration.
Swapping the parser to CSV without touching the constraint makes every CSV upload fail with an
IntegrityError, which the API reports as a bare 500 - exactly what happened in production.

`application/pdf` stays in the allowed set **as a historical value**: rows already imported
from PDF must remain valid data. It is the parser that no longer accepts new PDF uploads, so
the product decision lives in one place while history stays readable.
"""

from alembic import op

revision = "20261027_0004"
down_revision = "20261020_0003"
branch_labels = None
depends_on = None

SCHEMA = "b4"
TABLE = "roster_imports"
CONSTRAINT = "ck_roster_imports_media_type"

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

#: Values a stored import may carry. `text/csv` is the new accepted format; the pdf entry is
#: kept only so pre-existing rows keep satisfying the constraint.
ALLOWED = ("text/csv", XLSX, "application/pdf")


def upgrade() -> None:
    values = ", ".join(f"'{value}'" for value in ALLOWED)
    op.execute(f"ALTER TABLE {SCHEMA}.{TABLE} DROP CONSTRAINT IF EXISTS {CONSTRAINT}")
    op.execute(
        f"ALTER TABLE {SCHEMA}.{TABLE} ADD CONSTRAINT {CONSTRAINT} "
        f"CHECK (media_type IN ({values}))"
    )


def downgrade() -> None:
    # Dropping `text/csv` from the constraint would reject rows written since the upgrade, so
    # the downgrade refuses instead of deleting data or silently widening the set again.
    op.execute(f"ALTER TABLE {SCHEMA}.{TABLE} DROP CONSTRAINT IF EXISTS {CONSTRAINT}")
    op.execute(
        f"ALTER TABLE {SCHEMA}.{TABLE} ADD CONSTRAINT {CONSTRAINT} CHECK (media_type IN "
        f"('application/pdf', '{XLSX}'))"
    )
