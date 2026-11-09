"""Reminder campaigns: a teacher can nudge the students who have not submitted yet.

Revision ID: 20261108_0011
Revises: 20261108_0010
Create Date: 2026-11-09

Why this exists
---------------

`20261108_0010` created one campaign per assignment, keyed by `assignment_id UNIQUE`: the
publication announcement, materialised once and idempotent. The teacher page needs a second,
different message -- "you have not submitted yet" -- addressed to a subset of the roster and
repeatable, because a second nudge a week later is a legitimate act with its own recipients
and its own delivery evidence.

Keeping both in one row would destroy exactly the thing the campaign exists for: which
message reached whom. So this revision adds

* ``kind`` -- ``publication`` or ``reminder``, CHECK-constrained;
* ``note`` -- the teacher's optional sentence for a reminder (the fixed prompt is composed
  by the server and is deliberately not stored, so it cannot be edited away);
* replaces the unconditional ``UNIQUE (assignment_id)`` with the **partial** unique index
  ``UNIQUE (assignment_id) WHERE kind = 'publication'``: still one announcement per
  assignment, now many reminders.

Downgrade
---------

Deletes reminder campaigns and their delivery rows (child rows first: there is no ON DELETE
CASCADE here, on purpose -- delivery rows are evidence) and restores the unconditional unique
constraint. Reminders cannot be represented by the older schema at all, so keeping them was
not an option; the announcement campaigns are untouched.
"""

from __future__ import annotations

from alembic import op

revision = "20261108_0011"
down_revision = "20261108_0010"
branch_labels = None
depends_on = None

SCHEMA = "b4"
CAMPAIGNS = f"{SCHEMA}.assignment_notifications"
DELIVERIES = f"{SCHEMA}.assignment_notification_deliveries"


def upgrade() -> None:
    op.execute(
        f"""
        ALTER TABLE {CAMPAIGNS}
            ADD COLUMN kind text NOT NULL DEFAULT 'publication',
            ADD COLUMN note text NULL,
            ADD CONSTRAINT ck_assignment_notifications_kind
                CHECK (kind IN ('publication', 'reminder'))
        """
    )
    # `assignment_id` carried an inline UNIQUE; the announcement keeps it, reminders do not.
    op.execute(
        f"ALTER TABLE {CAMPAIGNS} "
        f"DROP CONSTRAINT IF EXISTS assignment_notifications_assignment_id_key"
    )
    op.execute(
        f"""
        CREATE UNIQUE INDEX uq_assignment_notifications_publication
            ON {CAMPAIGNS} (assignment_id)
            WHERE kind = 'publication'
        """
    )
    op.execute(
        f"CREATE INDEX ix_assignment_notifications_assignment_kind "
        f"ON {CAMPAIGNS} (assignment_id, kind)"
    )


def downgrade() -> None:
    op.execute(
        f"""
        DELETE FROM {DELIVERIES}
        WHERE notification_id IN (
            SELECT notification_id FROM {CAMPAIGNS} WHERE kind = 'reminder'
        )
        """
    )
    op.execute(f"DELETE FROM {CAMPAIGNS} WHERE kind = 'reminder'")
    op.execute("DROP INDEX IF EXISTS b4.ix_assignment_notifications_assignment_kind")
    op.execute("DROP INDEX IF EXISTS b4.uq_assignment_notifications_publication")
    op.execute(
        f"ALTER TABLE {CAMPAIGNS} "
        f"ADD CONSTRAINT assignment_notifications_assignment_id_key UNIQUE (assignment_id)"
    )
    op.execute(
        f"ALTER TABLE {CAMPAIGNS} "
        f"DROP CONSTRAINT IF EXISTS ck_assignment_notifications_kind"
    )
    op.execute(f"ALTER TABLE {CAMPAIGNS} DROP COLUMN note, DROP COLUMN kind")
