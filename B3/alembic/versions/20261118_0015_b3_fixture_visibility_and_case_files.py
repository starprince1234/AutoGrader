"""Add student_visible and optional test_case_id to b3.fixtures."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261118_0015"
down_revision = "20261118_0014"
branch_labels = None
depends_on = None

SCHEMA = "b3"


def upgrade() -> None:
    op.add_column(
        "fixtures",
        sa.Column(
            "student_visible",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "fixtures",
        sa.Column("test_case_id", sa.Uuid(), nullable=True),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        "fk_fixtures_test_case_id",
        "fixtures",
        "test_cases",
        ["test_case_id"],
        ["test_case_id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="CASCADE",
    )
    op.create_check_constraint(
        "ck_fixtures_student_visible_input_only",
        "fixtures",
        "NOT student_visible OR mount = 'input'",
        schema=SCHEMA,
    )
    op.drop_constraint(
        "uq_fixtures_question_version_id_path_mount",
        "fixtures",
        schema=SCHEMA,
        type_="unique",
    )
    op.execute(
        f"""
        CREATE UNIQUE INDEX uq_fixtures_version_path_mount
        ON {SCHEMA}.fixtures (question_version_id, path, mount)
        WHERE test_case_id IS NULL
        """
    )
    op.execute(
        f"""
        CREATE UNIQUE INDEX uq_fixtures_case_path_mount
        ON {SCHEMA}.fixtures (question_version_id, test_case_id, path, mount)
        WHERE test_case_id IS NOT NULL
        """
    )
    op.create_index("ix_fixtures_test_case_id", "fixtures", ["test_case_id"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_index("ix_fixtures_test_case_id", table_name="fixtures", schema=SCHEMA)
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.uq_fixtures_case_path_mount")
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.uq_fixtures_version_path_mount")
    op.create_unique_constraint(
        "uq_fixtures_question_version_id_path_mount",
        "fixtures",
        ["question_version_id", "path", "mount"],
        schema=SCHEMA,
    )
    op.drop_constraint(
        "ck_fixtures_student_visible_input_only",
        "fixtures",
        schema=SCHEMA,
        type_="check",
    )
    op.drop_constraint(
        "fk_fixtures_test_case_id",
        "fixtures",
        schema=SCHEMA,
        type_="foreignkey",
    )
    op.drop_column("fixtures", "test_case_id", schema=SCHEMA)
    op.drop_column("fixtures", "student_visible", schema=SCHEMA)
