"""Assignments may be closed: the status enum gains ``closed`` for a teacher's manual end.

Revision ID: 20261108_0011
Revises: 20261108_0010
Create Date: 2026-11-09

Why this exists
---------------

An assignment published **without** a ``due_at`` had no way to leave ``open``: nothing was
ever scheduled to move it, so it accepted submissions forever and the teacher page could
only describe that state, not change it. The teacher UI needs a real end action, and a real
end action has to be a server-side refusal rather than a flag the client honours.

``closed`` is that state, and it is enforced where admission is decided: B2 asks B3 for the
assignment's status on every submission and admits only ``open``, so flipping this one
column stops web and mail submissions. ``archived`` keeps its existing, separate meaning --
this revision widens the CHECK constraint, it does not redefine the other two values and it
adds no column: ``updated_at`` already records when the row moved.

Downgrade
---------

Restores the two-value constraint. A row that is ``closed`` cannot satisfy it, so the
downgrade first moves closed assignments back to ``open``: reopening them is the only
mapping the older schema can express, and silently deleting them would lose published
assignments. This is a dev-only rollback; the reopened assignments accept submissions again.
"""

from __future__ import annotations

from alembic import op

revision = "20261108_0011"
down_revision = "20261108_0010"
branch_labels = None
depends_on = None

SCHEMA = "b3"


def upgrade() -> None:
    op.execute(
        f"ALTER TABLE {SCHEMA}.assignments DROP CONSTRAINT IF EXISTS ck_assignments_status"
    )
    op.execute(
        f"""
        ALTER TABLE {SCHEMA}.assignments
            ADD CONSTRAINT ck_assignments_status
                CHECK (status IN ('open', 'closed', 'archived'))
        """
    )


def downgrade() -> None:
    # Reopen first, while the widened constraint is still in place: `closed` has no meaning
    # under the older schema, and deleting the rows instead would lose published assignments.
    op.execute(f"UPDATE {SCHEMA}.assignments SET status = 'open' WHERE status = 'closed'")
    op.execute(
        f"ALTER TABLE {SCHEMA}.assignments DROP CONSTRAINT IF EXISTS ck_assignments_status"
    )
    op.execute(
        f"""
        ALTER TABLE {SCHEMA}.assignments
            ADD CONSTRAINT ck_assignments_status
                CHECK (status IN ('open', 'archived'))
        """
    )
