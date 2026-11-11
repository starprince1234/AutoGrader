"""Assignment targets return: many sections plus an optional student narrowing list.

Revision ID: 20261108_0012
Revises: 20261108_0011
Create Date: 2026-11-11

Why this exists
---------------

Teacher requirement (round 4): an assignment must say **who it is for** — 1..N class
sections and, optionally, named students inside them — instead of "every effective student
of the course". The course-level model (``20261108_0010``) was a deliberate earlier
decision, and ``20261107_0008`` had a target model before it; this revision restores the
tables of 0008 with **one deliberate semantic change**:

* ``b3.assignment_sections`` — the target sections. **Zero rows means the whole course.**
  0008 required at least one row (its write path refused an empty target); that rule cannot
  express "historical assignment, nobody ever chose a target", which is exactly the state
  every assignment created between 0010 and 0012 is in. The write path now enforces
  "sections ⊆ course" instead, and the empty-target case is simply not represented here.
* ``b3.assignment_students`` — the optional narrowing list. No row still means "everyone in
  the target sections", so "not narrowed" and "narrowed to nobody" stay distinct.

History is preserved by **not touching it**: rows created while the course-level model was
in force keep no target rows, and the read path interprets that as the whole course. No
backfill, no guessing.

Downgrade
---------

Symmetric and lossless in the only sense that matters to this revision: it drops the two
target tables again. The assignments themselves are untouched either way, so a downgrade
returns to exactly the course-level model (targets are lost, by definition of the change).
"""

from __future__ import annotations

from alembic import op

revision = "20261108_0012"
down_revision = "20261108_0011"
branch_labels = None
depends_on = None

SCHEMA = "b3"
NEW_TABLES = ("assignment_sections", "assignment_students")


def upgrade() -> None:
    _create_assignment_sections()
    _create_assignment_students()
    _grant_privileges()


def downgrade() -> None:
    for table in reversed(NEW_TABLES):
        op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.{table}")


# --------------------------------------------------------------------------
# tables
# --------------------------------------------------------------------------


def _create_assignment_sections() -> None:
    """Target sections.

    No CHECK forces "at least one row": an empty target means the whole course, and a row
    count constraint cannot express that (0 rows is the historical default). The write path
    validates that every section belongs to the assignment's course.
    """
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
    """Optional narrowing list; zero rows means "everyone in the target sections"."""
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
    """Same shape as the earlier revisions: skip a role that does not exist.

    The ordinary role only reads targets (list/detail/audience assembly); the internal role
    writes them in the same transaction as the assignment. Neither table is updated in
    place, so no UPDATE/DELETE grant is handed out.
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
        $$;
        """
    )
