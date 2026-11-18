"""Roster import accepts XLS: widen the ``media_type`` CHECK constraint.

Revision ID: 20261118_0012
Revises: 20261108_0011
Create Date: 2026-11-18

The parser already accepts ``application/vnd.ms-excel`` (BIFF .xls), but
``ck_roster_imports_media_type`` only allowed ``text/csv``, ``xlsx``, and
``application/pdf``, causing every .xls upload to fail with an IntegrityError (500).
"""

from alembic import op

revision = "20261118_0012"
down_revision = "20261108_0011"
branch_labels = None
depends_on = None

SCHEMA = "b4"
TABLE = "roster_imports"
CONSTRAINT = "ck_roster_imports_media_type"

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
XLS = "application/vnd.ms-excel"

ALLOWED = ("text/csv", XLSX, XLS, "application/pdf")
PREVIOUS_ALLOWED = ("text/csv", XLSX, "application/pdf")


def upgrade() -> None:
    values = ", ".join(f"'{value}'" for value in ALLOWED)
    op.execute(f"ALTER TABLE {SCHEMA}.{TABLE} DROP CONSTRAINT IF EXISTS {CONSTRAINT}")
    op.execute(
        f"ALTER TABLE {SCHEMA}.{TABLE} ADD CONSTRAINT {CONSTRAINT} CHECK (media_type IN ({values}))"
    )


def downgrade() -> None:
    values = ", ".join(f"'{value}'" for value in PREVIOUS_ALLOWED)
    op.execute(f"ALTER TABLE {SCHEMA}.{TABLE} DROP CONSTRAINT IF EXISTS {CONSTRAINT}")
    op.execute(
        f"ALTER TABLE {SCHEMA}.{TABLE} ADD CONSTRAINT {CONSTRAINT} CHECK (media_type IN ({values}))"
    )
