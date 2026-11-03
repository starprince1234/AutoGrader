"""Optional course code.

Revision ID: 20261029_0006
Revises: 20261027_0005

See the module docstring of this change in the review notes: the course code was a NOT NULL
unique label, and screens rendered it next to the course name ("91 · 软件工程"), which read as if
the digits identified the course. Matching is by `course_id`/`section_id`, so the code is only a
label. It becomes nullable; uniqueness applies to the codes that exist.
"""

from alembic import op

revision = "20261029_0006"
down_revision = "20261027_0005"
branch_labels = None
depends_on = None

SCHEMA = "b4"


def upgrade() -> None:
    op.execute(f"ALTER TABLE {SCHEMA}.courses ALTER COLUMN course_code DROP NOT NULL")
    op.execute(
        f"ALTER TABLE {SCHEMA}.courses DROP CONSTRAINT IF EXISTS ck_courses_course_code_not_blank"
    )
    op.execute(f"ALTER TABLE {SCHEMA}.courses DROP CONSTRAINT IF EXISTS uq_courses_course_code")
    # Only real codes must be unique; NULL marks "this course has no code".
    op.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS uq_courses_course_code_present "
        f"ON {SCHEMA}.courses (course_code) WHERE course_code IS NOT NULL"
    )


def downgrade() -> None:
    # Refuse silently? No: the downgrade restores the constraint, and any codeless course makes
    # that impossible. Fail loudly instead of dropping data to satisfy a schema.
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.uq_courses_course_code_present")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM {SCHEMA}.courses WHERE course_code IS NULL) THEN
                RAISE EXCEPTION 'cannot restore NOT NULL: some courses have no code';
            END IF;
        END $$;
        """
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.courses ADD CONSTRAINT uq_courses_course_code UNIQUE (course_code)"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.courses ADD CONSTRAINT ck_courses_course_code_not_blank "
        f"CHECK (length(btrim(course_code)) > 0)"
    )
    op.execute(f"ALTER TABLE {SCHEMA}.courses ALTER COLUMN course_code SET NOT NULL")
