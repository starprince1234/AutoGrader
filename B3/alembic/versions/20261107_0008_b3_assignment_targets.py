"""Assignment targets: many sections plus an optional student narrowing list.

Revision ID: 20261107_0008
Revises: 20261107_0007
Create Date: 2026-11-07

Why this exists
---------------

An assignment used to point at exactly one ``section_id`` column. The assignment
rework (``MAIN/docs/architecture/assignment-workflow.md`` §1.2/§3.1) makes the target a
**set**: a course may hand the same homework to 1..N class sections and may narrow the
audience further to named students inside those sections. Two facts follow, and neither
fits in a column on ``assignments``:

* ``b3.assignment_sections`` -- the target sections (PK ``(assignment_id, section_id)``,
  one row per selected class); at least one row is required by the write path, which is
  why "assign to no class" is not representable.
* ``b3.assignment_students`` -- the optional narrowing list. **No row means "the whole
  target sections"**, so the two states "not narrowed" and "narrowed to nobody" cannot
  be confused: the latter is unrepresentable rather than an empty audience.

The old ``assignments.section_id`` column and ``ix_assignments_section_id`` are dropped:
keeping them would be a second, silently diverging answer to "who is this assignment
for". ``section_id`` remains a plain UUID (no cross-schema FK): the identity/course
module owns sections, and this migration never references another schema.

Downgrade
---------

Reversible, but the rollback is **lossy and dev-only** by construction: a set of
sections cannot be represented in one column. ``downgrade`` restores ``section_id``
with the **smallest target section** of each assignment (deterministic ``min`` over the
uuid's btree order), recreates the old index and drops both target tables -- every other
target section and every student narrowing row is discarded. An assignment with no
target row cannot be expressed at all; the write path guarantees 1..N rows, so such a
row can only come from hand-edited data, and inventing a section id would be worse than
letting the ``NOT NULL`` fail loudly.
"""

from __future__ import annotations

from alembic import op

revision = "20261107_0008"
down_revision = "20261107_0007"
branch_labels = None
depends_on = None

SCHEMA = "b3"
NEW_TABLES = ("assignment_sections", "assignment_students")


def upgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.ix_assignments_section_id")
    op.execute(f"ALTER TABLE {SCHEMA}.assignments DROP COLUMN section_id")
    _create_assignment_sections()
    _create_assignment_students()
    _grant_privileges()


def downgrade() -> None:
    # The column has to exist and be filled before it can be declared NOT NULL, and the
    # backfill has to run **before** the target table it reads is dropped.
    op.execute(f"ALTER TABLE {SCHEMA}.assignments ADD COLUMN section_id uuid")
    op.execute(
        f"""
        UPDATE {SCHEMA}.assignments AS assignment
           SET section_id = target.section_id
          FROM (
                SELECT DISTINCT ON (assignment_id) assignment_id, section_id
                  FROM {SCHEMA}.assignment_sections
                 ORDER BY assignment_id, section_id
               ) AS target
         WHERE target.assignment_id = assignment.assignment_id
        """
    )
    op.execute(f"ALTER TABLE {SCHEMA}.assignments ALTER COLUMN section_id SET NOT NULL")
    op.execute(
        f"CREATE INDEX ix_assignments_section_id ON {SCHEMA}.assignments (section_id)"
    )
    for table in reversed(NEW_TABLES):
        op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.{table}")


# --------------------------------------------------------------------------
# tables
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


# --------------------------------------------------------------------------
# privileges
# --------------------------------------------------------------------------


def _grant_privileges() -> None:
    """Extend the two roles' privileges to the tables this revision adds.

    Same shape as the earlier revisions: roles come from the module-local database
    bootstrap (and from the platform's identity module on the shared one), so a missing
    role is skipped rather than failing the migration.

    The ordinary role reads targets (list/detail responses are assembled from them) and
    never writes them; the internal role creates assignments and therefore inserts the
    target rows in the same transaction. Neither table is ever updated in place, so no
    UPDATE/DELETE grant is handed out.
    """
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'autograder_b3_app') THEN
                GRANT SELECT ON b3.assignment_sections TO autograder_b3_app;
                GRANT SELECT ON b3.assignment_students TO autograder_b3_app;
            END IF;

            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'autograder_b3_internal') THEN
                GRANT SELECT, INSERT ON b3.assignment_sections TO autograder_b3_internal;
                GRANT SELECT, INSERT ON b3.assignment_students TO autograder_b3_internal;
            END IF;
        END
        $$
        """
    )
