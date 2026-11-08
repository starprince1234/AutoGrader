"""Course-level assignments: drop the hand-picked target tables, record the notification hand-off.

Revision ID: 20261108_0010
Revises: 20261108_0009
Create Date: 2026-11-08

Why this exists
---------------

v0.32.0 makes an assignment **course-level**: it belongs to one course and automatically
applies to every section (课序), class (班级) and student of that course, including the ones
created later while it is open. The hand-picked target model introduced by ``0008``
(``b3.assignment_sections`` + ``b3.assignment_students``) is therefore removed: a target
list that no write path can change any more would be a second, silently diverging answer
to "who is this assignment for". The audience is now computed from the identity/course
module's live enrollments at read/submission time.

The same revision records the publication-notification hand-off on the assignment:

* ``notification_state`` -- ``pending`` (never attempted), ``scheduled`` (the identity/
  course module received the audience) or ``unavailable`` (it did not; a teacher may
  retry). A CHECK constraint keeps the enum closed.
* ``notification_scheduled_at`` -- when the hand-off succeeded; NULL otherwise.

Delivery itself belongs to B4 (its own notification tables); B3 stores only whether it
managed to hand the audience over.

Downgrade
---------

Recreates the two target tables (empty) and drops the notification columns, so the schema
shape is reversible. **The data is not**: the old model cannot represent a course-level
assignment, and inventing a target section/student for every existing assignment would
fabricate rows nobody chose. The downgrade therefore deletes the assignment and
assignment-item rows before restoring the old shape -- a rollback below this revision is
dev-only and must not be run against data worth keeping. Stating that here is preferable to
a backfill that silently targets the wrong class.
"""

from __future__ import annotations

from alembic import op

revision = "20261108_0010"
down_revision = "20261108_0009"
branch_labels = None
depends_on = None

SCHEMA = "b3"


def upgrade() -> None:
    # The old model's data cannot be represented any more; the rows go with the tables.
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.assignment_students")
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.assignment_sections")
    op.execute(
        f"""
        ALTER TABLE {SCHEMA}.assignments
            ADD COLUMN notification_state text NOT NULL DEFAULT 'pending',
            ADD COLUMN notification_scheduled_at timestamptz(3) NULL,
            ADD CONSTRAINT ck_assignments_notification_state
                CHECK (notification_state IN ('pending', 'scheduled', 'unavailable'))
        """
    )


def downgrade() -> None:
    op.execute(f"ALTER TABLE {SCHEMA}.assignments DROP CONSTRAINT ck_assignments_notification_state")
    op.execute(
        f"""
        ALTER TABLE {SCHEMA}.assignments
            DROP COLUMN notification_scheduled_at,
            DROP COLUMN notification_state
        """
    )
    # See the module docstring: a course-level assignment has no target list to restore, so
    # the rows are deleted rather than filled with invented sections/students. Items first:
    # their foreign key does not cascade.
    op.execute(f"DELETE FROM {SCHEMA}.assignment_items")
    op.execute(f"DELETE FROM {SCHEMA}.assignments")
    _create_assignment_sections()
    _create_assignment_students()
    _grant_privileges()


# --------------------------------------------------------------------------
# restored pre-v0.32 target tables (shape of revision 0008)
# --------------------------------------------------------------------------


def _create_assignment_sections() -> None:
    op.execute(
        f"""
        CREATE TABLE {SCHEMA}.assignment_sections (
            assignment_id uuid NOT NULL,
            section_id uuid NOT NULL,
            created_at timestamptz(3) NOT NULL,
            CONSTRAINT pk_assignment_sections PRIMARY KEY (assignment_id, section_id),
            CONSTRAINT fk_assignment_sections_assignment_id
                FOREIGN KEY (assignment_id)
                REFERENCES {SCHEMA}.assignments (assignment_id)
        )
        """
    )
    op.execute(
        f"CREATE INDEX ix_assignment_sections_section_id "
        f"ON {SCHEMA}.assignment_sections (section_id)"
    )


def _create_assignment_students() -> None:
    op.execute(
        f"""
        CREATE TABLE {SCHEMA}.assignment_students (
            assignment_id uuid NOT NULL,
            student_id uuid NOT NULL,
            created_at timestamptz(3) NOT NULL,
            CONSTRAINT pk_assignment_students PRIMARY KEY (assignment_id, student_id),
            CONSTRAINT fk_assignment_students_assignment_id
                FOREIGN KEY (assignment_id)
                REFERENCES {SCHEMA}.assignments (assignment_id)
        )
        """
    )
    op.execute(
        f"CREATE INDEX ix_assignment_students_student_id "
        f"ON {SCHEMA}.assignment_students (student_id)"
    )


def _grant_privileges() -> None:
    """The same least-privilege grants revision 0008 handed the recreated tables."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'autograder_b3_app') THEN
                GRANT SELECT ON {SCHEMA}.assignment_sections TO autograder_b3_app;
                GRANT SELECT ON {SCHEMA}.assignment_students TO autograder_b3_app;
            END IF;

            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'autograder_b3_internal') THEN
                GRANT SELECT, INSERT ON {SCHEMA}.assignment_sections TO autograder_b3_internal;
                GRANT SELECT, INSERT ON {SCHEMA}.assignment_students TO autograder_b3_internal;
            END IF;
        END
        $$
        """
    )
